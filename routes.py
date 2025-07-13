from flask import render_template, request, jsonify, redirect, url_for, flash, send_file
from app import app, db
from models import SearchHistory, Channel, PlaylistExport
from m3u_validator import M3UValidator
from web_scraper import get_website_text_content
from datetime import datetime
import re
import io
import threading
import time
import os

@app.route('/')
def index():
    recent_searches = SearchHistory.query.order_by(SearchHistory.search_date.desc()).limit(5).all()
    return render_template('index.html', recent_searches=recent_searches)

@app.route('/search', methods=['GET', 'POST'])
def search():
    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        if not url:
            flash('Por favor, insira uma URL válida.', 'error')
            return redirect(url_for('search'))
        
        # Create search history entry
        search_entry = SearchHistory(url=url, status='processing')
        db.session.add(search_entry)
        db.session.commit()
        
        # Start background processing
        thread = threading.Thread(target=process_playlist, args=(search_entry.id, url))
        thread.daemon = True
        thread.start()
        
        flash('Processamento iniciado! Atualize a página para ver o progresso.', 'info')
        return redirect(url_for('validate', search_id=search_entry.id))
    
    return render_template('search.html')

@app.route('/validate/<int:search_id>')
def validate(search_id):
    search_entry = SearchHistory.query.get_or_404(search_id)
    channels = Channel.query.filter_by(search_history_id=search_id).all()
    
    # Group channels by category
    categories = {}
    for channel in channels:
        category = channel.category or 'Sem Categoria'
        if category not in categories:
            categories[category] = []
        categories[category].append(channel)
    
    return render_template('validate.html', search_entry=search_entry, categories=categories)

@app.route('/history')
def history():
    searches = SearchHistory.query.order_by(SearchHistory.search_date.desc()).all()
    return render_template('history.html', searches=searches)

@app.route('/api/search_status/<int:search_id>')
def search_status(search_id):
    search_entry = SearchHistory.query.get_or_404(search_id)
    channels = Channel.query.filter_by(search_history_id=search_id).all()
    
    return jsonify({
        'status': search_entry.status,
        'channels_found': len(channels),
        'valid_channels': len([c for c in channels if c.is_working == True]),
        'title': search_entry.title
    })

@app.route('/api/test_channel/<int:channel_id>')
def test_channel(channel_id):
    channel = Channel.query.get_or_404(channel_id)
    validator = M3UValidator()
    
    # Test channel in background
    thread = threading.Thread(target=test_channel_connectivity, args=(channel_id,))
    thread.daemon = True
    thread.start()
    
    return jsonify({'status': 'testing'})

@app.route('/export/<int:search_id>')
def export_playlist(search_id):
    search_entry = SearchHistory.query.get_or_404(search_id)
    channels = Channel.query.filter_by(search_history_id=search_id, is_working=True).all()
    
    # Generate M3U content
    m3u_content = "#EXTM3U\n"
    for channel in channels:
        m3u_content += f"#EXTINF:-1"
        if channel.category:
            m3u_content += f" group-title=\"{channel.category}\""
        if channel.logo:
            m3u_content += f" tvg-logo=\"{channel.logo}\""
        m3u_content += f",{channel.name}\n"
        m3u_content += f"{channel.url}\n"
    
    # Save export
    export = PlaylistExport(
        filename=f"playlist_{search_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.m3u",
        content=m3u_content,
        channels_count=len(channels)
    )
    db.session.add(export)
    db.session.commit()
    
    # Return file
    return send_file(
        io.BytesIO(m3u_content.encode('utf-8')),
        mimetype='application/x-mpegurl',
        as_attachment=True,
        download_name=export.filename
    )

def process_playlist(search_id, url):
    """Background task to process playlist"""
    with app.app_context():
        try:
            search_entry = SearchHistory.query.get(search_id)
            validator = M3UValidator()
            
            # Try to fetch content
            content = None
            if url.endswith('.m3u') or url.endswith('.m3u8'):
                content = validator.fetch_m3u_content(url)
            else:
                # Try to scrape website for M3U content
                try:
                    web_content = get_website_text_content(url)
                    if web_content and '#EXTM3U' in web_content:
                        content = web_content
                except Exception as e:
                    app.logger.error(f"Error scraping website: {e}")
            
            if not content:
                search_entry.status = 'failed'
                search_entry.title = 'Erro ao buscar conteúdo'
                db.session.commit()
                return
            
            # Parse M3U content
            channels_data = validator.parse_m3u_content(content)
            
            # Extract title from content
            title_match = re.search(r'#EXTM3U.*?title="([^"]*)"', content, re.IGNORECASE)
            if title_match:
                search_entry.title = title_match.group(1)
            else:
                search_entry.title = f"Lista IPTV - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            
            # Save channels
            for channel_data in channels_data:
                channel = Channel(
                    name=channel_data['name'],
                    url=channel_data['url'],
                    category=channel_data.get('category'),
                    logo=channel_data.get('logo'),
                    group=channel_data.get('group'),
                    search_history_id=search_id
                )
                db.session.add(channel)
            
            search_entry.channels_found = len(channels_data)
            search_entry.status = 'completed'
            db.session.commit()
            
            # Start testing channels
            test_all_channels(search_id)
            
        except Exception as e:
            app.logger.error(f"Error processing playlist: {e}")
            search_entry = SearchHistory.query.get(search_id)
            search_entry.status = 'failed'
            search_entry.title = f'Erro: {str(e)}'
            db.session.commit()

def test_all_channels(search_id):
    """Test all channels for a search"""
    with app.app_context():
        channels = Channel.query.filter_by(search_history_id=search_id).all()
        validator = M3UValidator()
        
        for channel in channels:
            try:
                is_working = validator.test_stream_connectivity(channel.url)
                channel.is_working = is_working
                channel.last_checked = datetime.utcnow()
                time.sleep(0.1)  # Small delay to avoid overwhelming servers
            except Exception as e:
                app.logger.error(f"Error testing channel {channel.name}: {e}")
                channel.is_working = False
                channel.last_checked = datetime.utcnow()
        
        db.session.commit()
        
        # Update search entry
        search_entry = SearchHistory.query.get(search_id)
        search_entry.valid_channels = len([c for c in channels if c.is_working == True])
        db.session.commit()

def test_channel_connectivity(channel_id):
    """Test single channel connectivity"""
    with app.app_context():
        channel = Channel.query.get(channel_id)
        if channel:
            validator = M3UValidator()
            try:
                is_working = validator.test_stream_connectivity(channel.url)
                channel.is_working = is_working
                channel.last_checked = datetime.utcnow()
                db.session.commit()
            except Exception as e:
                app.logger.error(f"Error testing channel {channel.name}: {e}")
                channel.is_working = False
                channel.last_checked = datetime.utcnow()
                db.session.commit()

@app.route('/m3u_viewer')
def m3u_viewer():
    """Render M3U viewer page"""
    return render_template('m3u_viewer.html', m3u_content='')

@app.route('/m3u_viewer/<path:filename>')
def m3u_viewer_file(filename):
    """Render M3U viewer page with file content"""
    try:
        # Read the M3U file content
        file_path = os.path.join('attached_assets', filename)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                m3u_content = f.read()
        else:
            m3u_content = ''
        
        return render_template('m3u_viewer.html', m3u_content=m3u_content)
    except Exception as e:
        app.logger.error(f"Error loading M3U file: {e}")
        return render_template('m3u_viewer.html', m3u_content='')

@app.route('/m3u_viewer_upload', methods=['GET', 'POST'])
def m3u_viewer_upload():
    """Upload and view M3U file"""
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('Nenhum arquivo selecionado', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('Nenhum arquivo selecionado', 'error')
            return redirect(request.url)
        
        if file and file.filename.lower().endswith(('.m3u', '.m3u8', '.txt')):
            try:
                m3u_content = file.read().decode('utf-8')
                return render_template('m3u_viewer.html', m3u_content=m3u_content)
            except Exception as e:
                flash(f'Erro ao processar arquivo: {e}', 'error')
                return redirect(request.url)
        else:
            flash('Formato de arquivo não suportado. Use .m3u, .m3u8 ou .txt', 'error')
            return redirect(request.url)
    
    return render_template('m3u_upload.html')

@app.route('/demo_m3u')
def demo_m3u():
    """Demo page with the provided M3U content"""
    demo_content = """#EXTM3U
#EXTINF:-1 tvg-id="" tvg-name="Kill O Massacre no Trem" tvg-logo="https://image.tmdb.org/t/p/w400/mxgrtJvngNhppjCwu2AvdCtvSXa.jpg" group-title="(VOD BR) Filmes",Kill O Massacre no Trem
https://apiceplay.nexus/movie/20613489/69683690/1511266.mp4
#EXTINF:-1 tvg-id="" tvg-name="Martha" tvg-logo="https://image.tmdb.org/t/p/w400/4WN19oCTXlK8jXC0QZgNkBpcJcw.jpg" group-title="(VOD BR) Filmes",Martha
https://apiceplay.nexus/movie/20613489/69683690/1510096.mp4
#EXTINF:-1 tvg-id="" tvg-name="Sorria 2" tvg-logo="https://image.tmdb.org/t/p/w400/tPGaWmxdPumgOAzbGHu8aRJTaXx.jpg" group-title="(VOD BR) Filmes",Sorria 2
https://apiceplay.nexus/movie/20613489/69683690/1156771.mp4
#EXTINF:-1 tvg-id="" tvg-name="Grande Sertao" tvg-logo="https://image.tmdb.org/t/p/w400/ps1SI5yGewDlSBANLW5kzAqwQfT.jpg" group-title="(VOD BR) Filmes",Grande Sertao
https://apiceplay.nexus/movie/20613489/69683690/1156809.mp4
#EXTINF:-1 tvg-id="" tvg-name="Nao Fale o Mal" tvg-logo="https://image.tmdb.org/t/p/w400/cTcqksGeVKtHelStB7eKrSckczN.jpg" group-title="(VOD BR) Filmes",Nao Fale o Mal
https://apiceplay.nexus/movie/20613489/69683690/1157168.mp4
#EXTINF:-1 tvg-id="" tvg-name="Meu Filho Nosso Mundo" tvg-logo="https://image.tmdb.org/t/p/w400/iP1wYVjnBKUyeRyf2qDtP2GEB60.jpg" group-title="(VOD BR) Filmes",Meu Filho Nosso Mundo
https://apiceplay.nexus/movie/20613489/69683690/1157594.mp4
#EXTINF:-1 tvg-id="" tvg-name="Enfeiticados" tvg-logo="https://image.tmdb.org/t/p/w400/s9NYuPqsxxeoheDxSR419SS3Uf3.jpg" group-title="(VOD BR) Filmes",Enfeiticados
https://apiceplay.nexus/movie/20613489/69683690/1157929.mp4
#EXTINF:-1 tvg-id="" tvg-name="Robo Selvagem" tvg-logo="https://image.tmdb.org/t/p/w400/cWsd33Nwp3tgYB5LMMadl3qVMKh.jpg" group-title="(VOD BR) Filmes",Robo Selvagem
https://apiceplay.nexus/movie/20613489/69683690/1157930.mp4
#EXTINF:-1 tvg-id="" tvg-name="From the River to the Sea Um Filme Sobre a Guerra em Israel" tvg-logo="https://image.tmdb.org/t/p/w400/3iFmtROx5NIuXpJ1GuqvBZV3W7C.jpg" group-title="(VOD BR) Filmes",From the River to the Sea Um Filme Sobre a Guerra em Israel
https://apiceplay.nexus/movie/20613489/69683690/1157932.mp4
#EXTINF:-1 tvg-id="" tvg-name="Alice Subservience" tvg-logo="https://image.tmdb.org/t/p/w400/6EO0cjZt2vzAOmuDJZGED6GQmi4.jpg" group-title="(VOD BR) Filmes",Alice Subservience
https://apiceplay.nexus/movie/20613489/69683690/1511250.mp4
#EXTINF:-1 tvg-id="" tvg-name="Livre Encanto criminal" tvg-logo="https://image.tmdb.org/t/p/w400/cE7C87RQUMRVwAShtNeua49mHsy.jpg" group-title="(VOD BR) Filmes",Livre Encanto criminal
https://apiceplay.nexus/movie/20613489/69683690/1510098.mp4
#EXTINF:-1 tvg-id="" tvg-name="HAIKYU The Dumpster Battle" tvg-logo="https://image.tmdb.org/t/p/w400/pRfLrwbfUWmUj1Jh1NGLSQceHoT.jpg" group-title="(VOD BR) Filmes",HAIKYU The Dumpster Battle
https://apiceplay.nexus/movie/20613489/69683690/1510101.mp4
#EXTINF:-1 tvg-id="" tvg-name="Gigantes" tvg-logo="https://image.tmdb.org/t/p/w400/t1NFp7n2h2CTVC1KD5x2ZYFNfHl.jpg" group-title="(VOD BR) Filmes",Gigantes
https://apiceplay.nexus/movie/20613489/69683690/1510102.mp4
#EXTINF:-1 tvg-id="" tvg-name="O Quiosque" tvg-logo="https://image.tmdb.org/t/p/w400/eZExVc4fuAFSdh2VsHI1pN4Izqp.jpg" group-title="(VOD BR) Filmes",O Quiosque
https://apiceplay.nexus/movie/20613489/69683690/1510676.mp4
#EXTINF:-1 tvg-id="" tvg-name="SeAcabo Diario das Campeas" tvg-logo="https://image.tmdb.org/t/p/w400/AbhsO718qA8qeHyF00FsvG7vt3f.jpg" group-title="(VOD BR) Filmes",SeAcabo Diario das Campeas
https://apiceplay.nexus/movie/20613489/69683690/1511132.mp4
#EXTINF:-1 tvg-id="" tvg-name="Cumplice em Fuga" tvg-logo="https://image.tmdb.org/t/p/w400/j0U1I5lz9ueoxqpCObKkKwS6clg.jpg" group-title="(VOD BR) Filmes",Cumplice em Fuga
https://apiceplay.nexus/movie/20613489/69683690/1511249.mp4
#EXTINF:-1 tvg-id="" tvg-name="Nosso Segredinho" tvg-logo="https://image.tmdb.org/t/p/w400/u2WzilmXnBEAuGjOWIuY1a7k6yA.jpg" group-title="(VOD BR) Filmes",Nosso Segredinho
https://apiceplay.nexus/movie/20613489/69683690/1511253.mp4
#EXTINF:-1 tvg-id="" tvg-name="A Porta do Porao" tvg-logo="https://image.tmdb.org/t/p/w400/jNLBN8Hbz6wkklvfgjW41PW4rd5.jpg" group-title="(VOD BR) Filmes",A Porta do Porao
https://apiceplay.nexus/movie/20613489/69683690/1511256.mp4
#EXTINF:-1 tvg-id="" tvg-name="Saudade fez Morada aqui Dentro" tvg-logo="https://image.tmdb.org/t/p/w400/bawj0qlu7msZUIvnxk8yGnL5SdD.jpg" group-title="(VOD BR) Filmes",Saudade fez Morada aqui Dentro
https://apiceplay.nexus/movie/20613489/69683690/1511262.mp4
#EXTINF:-1 tvg-id="" tvg-name="Quem Ve Cara" tvg-logo="https://image.tmdb.org/t/p/w400/3HoQz6lQzvWQ59sE02YNMN8zaUj.jpg" group-title="(VOD BR) Filmes",Quem Ve Cara
https://apiceplay.nexus/movie/20613489/69683690/1511263.mp4
#EXTINF:-1 tvg-id="" tvg-name="Fique Acordado" tvg-logo="https://image.tmdb.org/t/p/w400/izPVZlS3FcfVjiQ4kjQKppIwja0.jpg" group-title="(VOD BR) Filmes",Fique Acordado
https://apiceplay.nexus/movie/20613489/69683690/1135312.mp4"""
    
    return render_template('m3u_viewer.html', m3u_content=demo_content)

@app.route('/download_html')
def download_html():
    """Download HTML file with M3U content"""
    m3u_content = request.args.get('content', '')
    
    # Generate complete HTML file
    html_content = generate_standalone_html(m3u_content)
    
    # Create file in memory
    file_buffer = io.BytesIO()
    file_buffer.write(html_content.encode('utf-8'))
    file_buffer.seek(0)
    
    filename = f"visualizador_m3u_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    
    return send_file(
        file_buffer,
        mimetype='text/html',
        as_attachment=True,
        download_name=filename
    )

def generate_standalone_html(m3u_content):
    """Generate standalone HTML file with embedded M3U content"""
    html_template = '''<!DOCTYPE html>
<html lang="pt-BR" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Navegador de Filmes M3U</title>
    <link href="https://cdn.replit.com/agent/bootstrap-agent-dark-theme.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        .movie-card {
            height: 400px;
            border: 1px solid var(--bs-gray-700);
            border-radius: 12px;
            overflow: hidden;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            background: var(--bs-dark);
        }
        
        .movie-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.5);
        }
        
        .movie-poster {
            height: 250px;
            overflow: hidden;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            position: relative;
        }
        
        .movie-poster img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: opacity 0.3s ease;
        }
        
        .movie-poster .no-image {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: white;
            font-size: 2rem;
        }
        
        .movie-info {
            padding: 1rem;
            height: 150px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        
        .movie-title {
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 0.5rem;
            line-height: 1.2;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
        }
        
        .movie-actions {
            display: flex;
            gap: 0.5rem;
            margin-top: auto;
        }
        
        .search-container {
            position: sticky;
            top: 0;
            z-index: 100;
            background: var(--bs-dark);
            padding: 1rem 0;
            border-bottom: 1px solid var(--bs-gray-700);
        }
        
        .stats-container {
            background: var(--bs-gray-900);
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1rem;
        }
        
        .loading {
            text-align: center;
            padding: 3rem;
        }
        
        .movie-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1.5rem;
            margin-top: 1.5rem;
        }
        
        .category-badge {
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
        }
        
        .no-results {
            text-align: center;
            padding: 3rem;
            color: var(--bs-gray-400);
        }
        
        .no-results i {
            font-size: 4rem;
            margin-bottom: 1rem;
        }
        
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="search-container">
            <div class="row">
                <div class="col-12">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h1 class="h3 mb-0">
                            <i class="fas fa-film me-2"></i>
                            Navegador de Filmes M3U
                        </h1>
                        <div class="d-flex gap-2">
                            <button class="btn btn-outline-primary" onclick="toggleGridView()">
                                <i class="fas fa-th" id="view-icon"></i>
                            </button>
                            <button class="btn btn-outline-success" onclick="exportM3U()">
                                <i class="fas fa-download me-2"></i>Exportar M3U
                            </button>
                        </div>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-6 mb-3">
                            <div class="input-group">
                                <span class="input-group-text">
                                    <i class="fas fa-search"></i>
                                </span>
                                <input type="text" class="form-control" id="searchInput" 
                                       placeholder="Buscar filmes..." onkeyup="filterMovies()">
                            </div>
                        </div>
                        <div class="col-md-6 mb-3">
                            <select class="form-select" id="categoryFilter" onchange="filterMovies()">
                                <option value="">Todas as Categorias</option>
                            </select>
                        </div>
                    </div>
                    
                    <div class="stats-container">
                        <div class="row text-center">
                            <div class="col-6 col-md-3">
                                <div class="h4 mb-0 text-primary" id="totalMovies">0</div>
                                <small class="text-muted">Total de Filmes</small>
                            </div>
                            <div class="col-6 col-md-3">
                                <div class="h4 mb-0 text-success" id="visibleMovies">0</div>
                                <small class="text-muted">Filmes Visíveis</small>
                            </div>
                            <div class="col-6 col-md-3">
                                <div class="h4 mb-0 text-info" id="totalCategories">0</div>
                                <small class="text-muted">Categorias</small>
                            </div>
                            <div class="col-6 col-md-3">
                                <div class="h4 mb-0 text-warning" id="loadingProgress">0%</div>
                                <small class="text-muted">Carregamento</small>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="loading" id="loadingIndicator">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Carregando...</span>
            </div>
            <div class="mt-2">Carregando filmes...</div>
        </div>
        
        <div class="movie-grid" id="movieGrid" style="display: none;">
            <!-- Movies will be dynamically loaded here -->
        </div>
        
        <div class="no-results" id="noResults" style="display: none;">
            <i class="fas fa-search"></i>
            <h4>Nenhum filme encontrado</h4>
            <p>Tente ajustar os filtros de busca ou categoria</p>
        </div>
    </div>

    <!-- Modal para exibir detalhes do filme -->
    <div class="modal fade" id="movieModal" tabindex="-1">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="modalTitle"></h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="col-md-4">
                            <img id="modalPoster" class="img-fluid rounded" style="width: 100%;">
                        </div>
                        <div class="col-md-8">
                            <div class="mb-3">
                                <strong>Categoria:</strong> <span id="modalCategory"></span>
                            </div>
                            <div class="mb-3">
                                <strong>URL do Filme:</strong>
                                <div class="input-group">
                                    <input type="text" class="form-control" id="modalUrl" readonly>
                                    <button class="btn btn-outline-primary" onclick="copyUrl()">
                                        <i class="fas fa-copy"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="d-flex gap-2">
                                <a id="modalPlayButton" class="btn btn-success" target="_blank">
                                    <i class="fas fa-play me-2"></i>Reproduzir
                                </a>
                                <button class="btn btn-primary" onclick="downloadMovie()">
                                    <i class="fas fa-download me-2"></i>Download
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Global variables
        let allMovies = [];
        let filteredMovies = [];
        let categories = new Set();
        let currentGridView = 'grid';
        
        // M3U content embedded in the file
        const m3uContent = `{m3u_content}`;
        
        // Initialize the application
        document.addEventListener('DOMContentLoaded', function() {
            parseM3UContent();
            setupEventListeners();
        });
        
        function parseM3UContent() {
            const lines = m3uContent.split('\\n');
            let currentMovie = null;
            let processed = 0;
            
            for (let i = 0; i < lines.length; i++) {
                const line = lines[i].trim();
                
                if (line.startsWith('#EXTINF:')) {
                    currentMovie = parseExtinf(line);
                } else if (line && !line.startsWith('#') && currentMovie) {
                    currentMovie.url = line;
                    allMovies.push(currentMovie);
                    categories.add(currentMovie.category || 'Sem Categoria');
                    currentMovie = null;
                }
                
                // Update progress
                processed++;
                const progress = Math.round((processed / lines.length) * 100);
                document.getElementById('loadingProgress').textContent = progress + '%';
            }
            
            filteredMovies = [...allMovies];
            updateUI();
            hideLoading();
        }
        
        function parseExtinf(line) {
            const movie = {
                name: '',
                logo: '',
                category: '',
                url: ''
            };
            
            // Extract name (after last comma)
            const nameMatch = line.match(/,(.+)$/);
            if (nameMatch) {
                movie.name = nameMatch[1].trim();
            }
            
            // Extract logo
            const logoMatch = line.match(/tvg-logo="([^"]+)"/);
            if (logoMatch) {
                movie.logo = logoMatch[1];
            }
            
            // Extract category
            const categoryMatch = line.match(/group-title="([^"]+)"/);
            if (categoryMatch) {
                movie.category = categoryMatch[1];
            }
            
            return movie;
        }
        
        function updateUI() {
            updateStats();
            updateCategoryFilter();
            renderMovies();
        }
        
        function updateStats() {
            document.getElementById('totalMovies').textContent = allMovies.length;
            document.getElementById('visibleMovies').textContent = filteredMovies.length;
            document.getElementById('totalCategories').textContent = categories.size;
            document.getElementById('loadingProgress').textContent = '100%';
        }
        
        function updateCategoryFilter() {
            const categoryFilter = document.getElementById('categoryFilter');
            categoryFilter.innerHTML = '<option value="">Todas as Categorias</option>';
            
            Array.from(categories).sort().forEach(category => {
                const option = document.createElement('option');
                option.value = category;
                option.textContent = category;
                categoryFilter.appendChild(option);
            });
        }
        
        function renderMovies() {
            const movieGrid = document.getElementById('movieGrid');
            const noResults = document.getElementById('noResults');
            
            if (filteredMovies.length === 0) {
                movieGrid.style.display = 'none';
                noResults.style.display = 'block';
                return;
            }
            
            movieGrid.style.display = 'grid';
            noResults.style.display = 'none';
            movieGrid.innerHTML = '';
            
            filteredMovies.forEach((movie, index) => {
                const movieCard = createMovieCard(movie, index);
                movieGrid.appendChild(movieCard);
            });
        }
        
        function createMovieCard(movie, index) {
            const card = document.createElement('div');
            card.className = 'movie-card';
            card.innerHTML = `
                <div class="movie-poster">
                    ${movie.logo ? 
                        `<img src="${movie.logo}" alt="${movie.name}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                         <div class="no-image" style="display: none;">
                             <i class="fas fa-film"></i>
                         </div>` :
                        `<div class="no-image">
                             <i class="fas fa-film"></i>
                         </div>`
                    }
                    <div class="category-badge">${movie.category || 'Sem Categoria'}</div>
                </div>
                <div class="movie-info">
                    <div class="movie-title">${movie.name}</div>
                    <div class="movie-actions">
                        <button class="btn btn-sm btn-primary" onclick="showMovieDetails(${index})">
                            <i class="fas fa-info-circle"></i>
                        </button>
                        <button class="btn btn-sm btn-success" onclick="playMovie('${movie.url}')">
                            <i class="fas fa-play"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-secondary" onclick="copyMovieUrl('${movie.url}')">
                            <i class="fas fa-copy"></i>
                        </button>
                    </div>
                </div>
            `;
            
            return card;
        }
        
        function filterMovies() {
            const searchTerm = document.getElementById('searchInput').value.toLowerCase();
            const categoryFilter = document.getElementById('categoryFilter').value;
            
            filteredMovies = allMovies.filter(movie => {
                const matchesSearch = movie.name.toLowerCase().includes(searchTerm);
                const matchesCategory = !categoryFilter || movie.category === categoryFilter;
                return matchesSearch && matchesCategory;
            });
            
            renderMovies();
            updateStats();
        }
        
        function showMovieDetails(index) {
            const movie = filteredMovies[index];
            document.getElementById('modalTitle').textContent = movie.name;
            document.getElementById('modalCategory').textContent = movie.category || 'Sem Categoria';
            document.getElementById('modalUrl').value = movie.url;
            document.getElementById('modalPlayButton').href = movie.url;
            
            if (movie.logo) {
                document.getElementById('modalPoster').src = movie.logo;
            }
            
            new bootstrap.Modal(document.getElementById('movieModal')).show();
        }
        
        function playMovie(url) {
            window.open(url, '_blank');
        }
        
        function copyMovieUrl(url) {
            navigator.clipboard.writeText(url).then(() => {
                showToast('URL copiada!', 'A URL do filme foi copiada para a área de transferência.');
            });
        }
        
        function copyUrl() {
            const urlInput = document.getElementById('modalUrl');
            urlInput.select();
            document.execCommand('copy');
            showToast('URL copiada!', 'A URL foi copiada para a área de transferência.');
        }
        
        function downloadMovie() {
            const url = document.getElementById('modalUrl').value;
            const a = document.createElement('a');
            a.href = url;
            a.download = '';
            a.click();
        }
        
        function exportM3U() {
            const m3uContent = generateM3UContent();
            const blob = new Blob([m3uContent], { type: 'application/x-mpegurl' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'filmes_' + new Date().toISOString().split('T')[0] + '.m3u';
            a.click();
            URL.revokeObjectURL(url);
        }
        
        function generateM3UContent() {
            let content = '#EXTM3U\\n';
            filteredMovies.forEach(movie => {
                content += `#EXTINF:-1 tvg-name="${movie.name}"`;
                if (movie.logo) content += ` tvg-logo="${movie.logo}"`;
                if (movie.category) content += ` group-title="${movie.category}"`;
                content += `,${movie.name}\\n`;
                content += `${movie.url}\\n`;
            });
            return content;
        }
        
        function toggleGridView() {
            const movieGrid = document.getElementById('movieGrid');
            const viewIcon = document.getElementById('view-icon');
            
            if (currentGridView === 'grid') {
                movieGrid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(200px, 1fr))';
                viewIcon.className = 'fas fa-list';
                currentGridView = 'compact';
            } else {
                movieGrid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(280px, 1fr))';
                viewIcon.className = 'fas fa-th';
                currentGridView = 'grid';
            }
        }
        
        function hideLoading() {
            document.getElementById('loadingIndicator').style.display = 'none';
            document.getElementById('movieGrid').style.display = 'grid';
        }
        
        function setupEventListeners() {
            // Keyboard shortcuts
            document.addEventListener('keydown', function(e) {
                if (e.ctrlKey && e.key === 'f') {
                    e.preventDefault();
                    document.getElementById('searchInput').focus();
                }
            });
        }
        
        function showToast(title, message) {
            // Simple toast notification
            const toast = document.createElement('div');
            toast.className = 'toast-notification';
            toast.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                background: var(--bs-success);
                color: white;
                padding: 1rem;
                border-radius: 8px;
                z-index: 9999;
                animation: slideIn 0.3s ease;
            `;
            toast.innerHTML = `<strong>${title}</strong><br>${message}`;
            document.body.appendChild(toast);
            
            setTimeout(() => {
                toast.remove();
            }, 3000);
        }
    </script>
</body>
</html>'''
    
    # Replace the M3U content placeholder
    return html_template.replace('{m3u_content}', m3u_content.replace('`', '\\`').replace('\\', '\\\\'))

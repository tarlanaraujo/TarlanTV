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

// IPTV Manager - Client-side JavaScript

// Global variables
let searchId = null;
let refreshInterval = null;

// Initialize application
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

function initializeApp() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize form validation
    initializeFormValidation();
    
    // Initialize auto-refresh for processing searches
    initializeAutoRefresh();
}

function initializeFormValidation() {
    const forms = document.querySelectorAll('.needs-validation');
    
    forms.forEach(form => {
        form.addEventListener('submit', function(event) {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            form.classList.add('was-validated');
        });
    });
}

function initializeAutoRefresh() {
    // Check if we're on a validation page with processing status
    const statusBadge = document.getElementById('status-badge');
    if (statusBadge && statusBadge.textContent.includes('Processando')) {
        const pathParts = window.location.pathname.split('/');
        if (pathParts[1] === 'validate' && pathParts[2]) {
            searchId = parseInt(pathParts[2]);
            startAutoRefresh();
        }
    }
}

function startAutoRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
    }
    
    refreshInterval = setInterval(function() {
        refreshSearchStatus();
    }, 5000); // Refresh every 5 seconds
}

function stopAutoRefresh() {
    if (refreshInterval) {
        clearInterval(refreshInterval);
        refreshInterval = null;
    }
}

function refreshSearchStatus() {
    if (!searchId) return;
    
    fetch(`/api/search_status/${searchId}`)
        .then(response => response.json())
        .then(data => {
            updateStatusDisplay(data);
            
            if (data.status === 'completed') {
                stopAutoRefresh();
                // Reload page to show channels
                setTimeout(() => {
                    location.reload();
                }, 1000);
            }
        })
        .catch(error => {
            console.error('Error refreshing status:', error);
        });
}

function updateStatusDisplay(data) {
    // Update channels found
    const channelsFound = document.getElementById('channels-found');
    if (channelsFound) {
        channelsFound.textContent = data.channels_found;
    }
    
    // Update valid channels
    const validChannels = document.getElementById('valid-channels');
    if (validChannels) {
        validChannels.textContent = data.valid_channels;
    }
    
    // Update status badge
    const statusBadge = document.getElementById('status-badge');
    if (statusBadge) {
        statusBadge.className = 'badge ' + getStatusClass(data.status);
        statusBadge.innerHTML = getStatusText(data.status);
    }
    
    // Update title if available
    if (data.title) {
        const titleElement = document.querySelector('.card-title');
        if (titleElement) {
            titleElement.innerHTML = `<i class="fas fa-list me-2"></i>${data.title}`;
        }
    }
    
    // Hide processing alert if completed
    if (data.status === 'completed') {
        const processingAlert = document.getElementById('processing-alert');
        if (processingAlert) {
            processingAlert.style.display = 'none';
        }
    }
}

function getStatusClass(status) {
    switch (status) {
        case 'completed':
            return 'bg-success';
        case 'processing':
            return 'bg-warning';
        default:
            return 'bg-danger';
    }
}

function getStatusText(status) {
    switch (status) {
        case 'completed':
            return '<i class="fas fa-check me-1"></i>Concluído';
        case 'processing':
            return '<i class="fas fa-spinner fa-spin me-1"></i>Processando';
        default:
            return '<i class="fas fa-times me-1"></i>Erro';
    }
}

function testChannel(channelId) {
    const button = event.target.closest('button');
    const originalHtml = button.innerHTML;
    
    // Show loading state
    button.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Testando...';
    button.disabled = true;
    
    fetch(`/api/test_channel/${channelId}`)
        .then(response => response.json())
        .then(data => {
            if (data.status === 'testing') {
                showToast('Teste iniciado', 'O canal está sendo testado. Aguarde alguns segundos.', 'info');
                
                // Restore button after 3 seconds
                setTimeout(() => {
                    button.innerHTML = originalHtml;
                    button.disabled = false;
                }, 3000);
            }
        })
        .catch(error => {
            console.error('Error testing channel:', error);
            button.innerHTML = originalHtml;
            button.disabled = false;
            showToast('Erro', 'Erro ao testar canal. Tente novamente.', 'error');
        });
}

function showToast(title, message, type = 'info') {
    // Create toast container if it doesn't exist
    let toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }
    
    // Create toast element
    const toastId = 'toast-' + Date.now();
    const toastHtml = `
        <div id="${toastId}" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="toast-header">
                <i class="fas fa-${getToastIcon(type)} me-2 text-${getToastColor(type)}"></i>
                <strong class="me-auto">${title}</strong>
                <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
            <div class="toast-body">
                ${message}
            </div>
        </div>
    `;
    
    toastContainer.insertAdjacentHTML('beforeend', toastHtml);
    
    // Show toast
    const toastElement = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
    
    // Remove toast element after it's hidden
    toastElement.addEventListener('hidden.bs.toast', function() {
        toastElement.remove();
    });
}

function getToastIcon(type) {
    switch (type) {
        case 'success':
            return 'check-circle';
        case 'error':
            return 'exclamation-circle';
        case 'warning':
            return 'exclamation-triangle';
        default:
            return 'info-circle';
    }
}

function getToastColor(type) {
    switch (type) {
        case 'success':
            return 'success';
        case 'error':
            return 'danger';
        case 'warning':
            return 'warning';
        default:
            return 'info';
    }
}

// Utility functions
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(function() {
        showToast('Copiado', 'Texto copiado para a área de transferência', 'success');
    }, function(err) {
        console.error('Erro ao copiar: ', err);
        showToast('Erro', 'Erro ao copiar texto', 'error');
    });
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('pt-BR') + ' ' + date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function formatDuration(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainingSeconds = seconds % 60;
    
    if (hours > 0) {
        return `${hours}h ${minutes}m ${remainingSeconds}s`;
    } else if (minutes > 0) {
        return `${minutes}m ${remainingSeconds}s`;
    } else {
        return `${remainingSeconds}s`;
    }
}

// Export functions for global use
window.testChannel = testChannel;
window.refreshSearchStatus = refreshSearchStatus;
window.copyToClipboard = copyToClipboard;
window.showToast = showToast;

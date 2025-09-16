// Main JavaScript file for Deploy Platform

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize popovers
    const popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Project status indicators
    updateProjectStatuses();

    // Deployment log auto-refresh
    setupDeploymentLogRefresh();

    // Form validations
    setupFormValidations();
});

// Update project status indicators
function updateProjectStatuses() {
    const statusElements = document.querySelectorAll('.project-status');
    
    statusElements.forEach(element => {
        const status = element.getAttribute('data-status');
        if (status === 'running') {
            element.classList.add('status-running');
            element.innerHTML = '<i class="fas fa-check-circle"></i> Running';
        } else if (status === 'deploying') {
            element.classList.add('status-deploying');
            element.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Deploying';
        } else if (status === 'failed') {
            element.classList.add('status-failed');
            element.innerHTML = '<i class="fas fa-exclamation-circle"></i> Failed';
        }
    });
}

// Setup auto-refresh for deployment logs
function setupDeploymentLogRefresh() {
    const deploymentLog = document.getElementById('deployment-log');
    if (deploymentLog) {
        const deploymentId = deploymentLog.getAttribute('data-deployment-id');
        if (deploymentId) {
            // Auto-refresh every 5 seconds if deployment is in progress
            const isDeploying = deploymentLog.getAttribute('data-deploying') === 'true';
            if (isDeploying) {
                setInterval(() => {
                    fetchDeploymentLog(deploymentId);
                }, 5000);
            }
        }
    }
}

// Fetch deployment log updates
function fetchDeploymentLog(deploymentId) {
    fetch(`/api/deployments/${deploymentId}/log/`)
        .then(response => response.json())
        .then(data => {
            const deploymentLog = document.getElementById('deployment-log');
            if (deploymentLog) {
                deploymentLog.textContent = data.log;
                
                // Scroll to bottom of log
                deploymentLog.scrollTop = deploymentLog.scrollHeight;
                
                // Update status if deployment is complete
                if (data.status !== 'deploying') {
                    deploymentLog.setAttribute('data-deploying', 'false');
                    const statusBadge = document.getElementById('deployment-status');
                    if (statusBadge) {
                        statusBadge.className = ''; // Remove all classes
                        statusBadge.classList.add('badge', data.status === 'running' ? 'bg-success' : 'bg-danger');
                        statusBadge.textContent = data.status === 'running' ? 'Running' : 'Failed';
                    }
                }
            }
        })
        .catch(error => console.error('Error fetching deployment log:', error));
}

// Setup form validations
function setupFormValidations() {
    const forms = document.querySelectorAll('.needs-validation');
    
    Array.from(forms).forEach(form => {
        form.addEventListener('submit', event => {
            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }
            
            form.classList.add('was-validated');
        }, false);
    });
}

// Copy to clipboard functionality
function copyToClipboard(text, buttonElement) {
    navigator.clipboard.writeText(text).then(() => {
        const originalText = buttonElement.innerHTML;
        buttonElement.innerHTML = '<i class="fas fa-check"></i> Copied!';
        
        setTimeout(() => {
            buttonElement.innerHTML = originalText;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy text: ', err);
    });
}

// Toggle project visibility (public/private)
function toggleProjectVisibility(projectId, button) {
    const isPublic = button.getAttribute('data-public') === 'true';
    const newStatus = !isPublic;
    
    fetch(`/api/projects/${projectId}/toggle-visibility/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({ is_public: newStatus })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            button.setAttribute('data-public', newStatus.toString());
            button.innerHTML = newStatus ? 
                '<i class="fas fa-eye"></i> Make Private' : 
                '<i class="fas fa-eye-slash"></i> Make Public';
            
            // Show success message
            const messageContainer = document.getElementById('messages');
            if (messageContainer) {
                const alert = document.createElement('div');
                alert.className = 'alert alert-success alert-dismissible fade show';
                alert.innerHTML = `
                    Project visibility updated to ${newStatus ? 'public' : 'private'}.
                    <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                `;
                messageContainer.appendChild(alert);
                
                // Auto-dismiss after 3 seconds
                setTimeout(() => {
                    alert.classList.remove('show');
                    setTimeout(() => alert.remove(), 150);
                }, 3000);
            }
        }
    })
    .catch(error => console.error('Error toggling project visibility:', error));
}

// Get CSRF token from cookies
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
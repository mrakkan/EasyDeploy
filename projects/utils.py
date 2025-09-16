import os
import subprocess
import tempfile
import requests
import shutil
import random
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from .models import Deployment

def get_github_user_token(user):
    """
    Get GitHub access token for a user
    """
    try:
        social_account = SocialAccount.objects.get(user=user, provider='github')
        token = social_account.socialtoken_set.first()
        if token:
            return token.token
        return None
    except (SocialAccount.DoesNotExist, AttributeError):
        return None

def get_user_repositories(user):
    """
    Get list of repositories for a user from GitHub API
    """
    token = get_github_user_token(user)
    if not token:
        return []
    
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    response = requests.get('https://api.github.com/user/repos', headers=headers)
    
    if response.status_code == 200:
        repos = response.json()
        # Filter and format repository data
        return [{
            'id': repo['id'],
            'name': repo['name'],
            'full_name': repo['full_name'],
            'description': repo['description'],
            'html_url': repo['html_url'],
            'clone_url': repo['clone_url'],
            'default_branch': repo['default_branch'],
            'private': repo['private'],
            'created_at': repo['created_at'],
            'updated_at': repo['updated_at'],
        } for repo in repos]
    
    return []

def create_deployment(project, status, log):
    """
    Create a new deployment record
    """
    deployment = Deployment.objects.create(
        project=project,
        status=status,
        log=log
    )
    return deployment

def update_deployment(deployment, status, log):
    """
    Update an existing deployment record
    """
    deployment.status = status
    deployment.log += f'\n{log}'
    deployment.save()
    return deployment

def deploy_project_from_github(project, user):
    """
    Deploy a project from GitHub repository using Docker
    """
    # Create a new deployment record first
    deployment = create_deployment(project, 'in_progress', 'Starting deployment...')
    
    # Always check for GitHub token
    token = get_github_user_token(user)
    if not token:
        return update_deployment(deployment, 'failed', 'GitHub token not found. Please connect your GitHub account.')
    
    try:
        # Create a temporary directory for the repository
        temp_dir = tempfile.mkdtemp()
        
        # Always use token for authentication
        clone_url = project.github_repo_url.replace('https://', f'https://{token}@')
        update_deployment(deployment, 'in_progress', f'Cloning repository from {project.github_repo_url} using authentication...')
        
        try:
            result = subprocess.run(
                ['git', 'clone', clone_url, temp_dir],
                capture_output=True,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            return update_deployment(deployment, 'failed', f'Failed to clone repository: {e.stderr}')
        
        # Get the latest commit hash
        original_dir = os.getcwd()
        os.chdir(temp_dir)
        
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                capture_output=True,
                text=True,
                check=True
            )
            commit_hash = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            os.chdir(original_dir)
            return update_deployment(deployment, 'failed', f'Failed to get commit hash: {e.stderr}')
        
        # Update deployment with commit hash
        deployment.commit_hash = commit_hash
        deployment.save()
        
        update_deployment(deployment, 'in_progress', f'Repository cloned successfully. Commit: {commit_hash[:8]}')
        
        # Check for Dockerfile
        if not os.path.exists(os.path.join(temp_dir, 'Dockerfile')):
            return update_deployment(deployment, 'failed', 'Dockerfile not found in the repository. Please add a Dockerfile to your project.')
        
        # Generate a unique container name based on project ID and timestamp
        container_name = f'deploy-{project.id}-{int(deployment.timestamp.timestamp())}'
        
        # Build Docker image
        update_deployment(deployment, 'in_progress', 'Building Docker image...')
        try:
            build_result = subprocess.run(
                ['docker', 'build', '-t', container_name, '.'],
                capture_output=True,
                text=True,
                check=True
            )
            update_deployment(deployment, 'in_progress', f'Docker build output:\n{build_result.stdout}')
        except subprocess.CalledProcessError as e:
            return update_deployment(deployment, 'failed', f'Failed to build Docker image:\n{e.stderr}')
        
        # Run Docker container
        update_deployment(deployment, 'in_progress', 'Starting Docker container...')
        try:
            # Stop any existing container with the same name
            subprocess.run(['docker', 'stop', container_name], capture_output=True, text=True)
            subprocess.run(['docker', 'rm', container_name], capture_output=True, text=True)
            
            # Run the new container
            port = project.exposed_port or random.randint(8000, 9000)
            run_result = subprocess.run(
                ['docker', 'run', '-d', '--name', container_name, '-p', f'{port}:80', container_name],
                capture_output=True,
                text=True,
                check=True
            )
            container_id = run_result.stdout.strip()
            update_deployment(deployment, 'in_progress', f'Container started with ID: {container_id}')
            
            # Generate preview URL
            preview_url = f'http://localhost:{port}'
            deployment.preview_url = preview_url
            deployment.save()
            
            # Update project status
            project.status = 'running'
            project.exposed_port = port
            project.save()
            
            return update_deployment(deployment, 'success', f'Deployment completed successfully. Your project is available at {preview_url}')
        except subprocess.CalledProcessError as e:
            return update_deployment(deployment, 'failed', f'Failed to run Docker container:\n{e.stderr}')
    
    except Exception as e:
        return update_deployment(deployment, 'failed', f'Deployment failed: {str(e)}')
    
    finally:
        # Return to original directory
        if 'original_dir' in locals():
            os.chdir(original_dir)
        
        # Clean up temporary directory
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from .models import Project, Deployment, Tag
from .forms import ProjectForm, TagForm
import random
import requests
from allauth.socialaccount.models import SocialAccount, SocialToken
import json
import os
import subprocess
import tempfile
import shutil

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

def project_list(request):
    """
    List all projects for the current user
    """
    if not request.user.is_authenticated:
        return redirect('accounts:login')
        
    projects = Project.objects.filter(owner=request.user).order_by('-created_at')
    return render(request, 'projects/project_list.html', {'projects': projects})

@login_required
def project_create(request):
    """
    Create a new project
    """
    # Check if user wants to deploy from GitHub
    source = request.GET.get('source')
    from_github = source == 'github'
    
    # Check if user has connected GitHub account
    has_github = False
    github_repos = []
    
    if from_github:
        try:
            from allauth.socialaccount.models import SocialAccount, SocialToken
            import requests
            
            # Get GitHub account
            github_account = SocialAccount.objects.get(user=request.user, provider='github')
            has_github = True
            
            # Get GitHub token
            try:
                token = SocialToken.objects.get(account=github_account)
                
                # Fetch repositories from GitHub API
                headers = {
                    'Authorization': f'token {token.token}',
                    'Accept': 'application/vnd.github.v3+json'
                }
                response = requests.get('https://api.github.com/user/repos', headers=headers)
                
                if response.status_code == 200:
                    github_repos = response.json()
                else:
                    messages.error(request, f'ไม่สามารถดึงข้อมูล repositories จาก GitHub ได้: {response.status_code}')
            except SocialToken.DoesNotExist:
                messages.warning(request, 'ไม่พบ GitHub token กรุณาเชื่อมต่อบัญชี GitHub ใหม่อีกครั้ง')
                return redirect('socialaccount_connections')
        except SocialAccount.DoesNotExist:
            messages.warning(request, 'คุณยังไม่ได้เชื่อมต่อบัญชี GitHub กรุณาเชื่อมต่อบัญชีก่อน')
            return redirect('socialaccount_connections')
        except Exception as e:
            messages.error(request, f'เกิดข้อผิดพลาด: {str(e)}')
    
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.owner = request.user
            
            # Assign a random port between 8001-8999
            used_ports = Project.objects.exclude(exposed_port__isnull=True).values_list('exposed_port', flat=True)
            available_ports = [p for p in range(8001, 9000) if p not in used_ports]
            if available_ports:
                project.exposed_port = random.choice(available_ports)
            else:
                messages.error(request, 'ไม่มีพอร์ตว่างสำหรับโปรเจกต์ใหม่')
                return render(request, 'projects/project_form.html', {'form': form})
                
            project.save()
            form.save_m2m()  # Save the many-to-many relationships
            messages.success(request, f'โปรเจกต์ {project.name} ถูกสร้างเรียบร้อยแล้ว')
            return redirect('projects:project_detail', pk=project.pk)
    else:
        form = ProjectForm()
    
    if from_github and has_github and github_repos:
        # Redirect to GitHub repositories page
        return redirect('projects:github_repositories')
    
    return render(request, 'projects/project_form.html', {
        'form': form, 
        'title': 'สร้างโปรเจกต์ใหม่',
        'from_github': from_github,
        'has_github': has_github,
        'github_repos': github_repos
    })

@login_required
def project_detail(request, pk):
    """
    View project details
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner or if the project is public
    if project.owner != request.user and not project.is_public:
        messages.error(request, 'คุณไม่มีสิทธิ์เข้าถึงโปรเจกต์นี้')
        return redirect('projects:project_list')
        
    deployments = Deployment.objects.filter(project=project).order_by('-timestamp')
    return render(request, 'projects/project_detail.html', {'project': project, 'deployments': deployments})
    
@login_required
def github_repositories(request):
    """
    List GitHub repositories for the current user
    """
    try:
        # Get GitHub account and token
        github_account = SocialAccount.objects.get(user=request.user, provider='github')
        token = SocialToken.objects.get(account=github_account)
        
        # Fetch repositories from GitHub API
        headers = {
            'Authorization': f'token {token.token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        response = requests.get('https://api.github.com/user/repos', headers=headers)
        
        if response.status_code == 200:
            repositories = response.json()
            return render(request, 'projects/github_repositories.html', {'repositories': repositories})
        else:
            messages.error(request, f'ไม่สามารถดึงข้อมูล repositories จาก GitHub ได้: {response.status_code}')
            return redirect('projects:project_create')
            
    except SocialAccount.DoesNotExist:
        messages.warning(request, 'คุณยังไม่ได้เชื่อมต่อบัญชี GitHub กรุณาเชื่อมต่อบัญชีก่อน')
        return redirect('socialaccount_connections')
    except SocialToken.DoesNotExist:
        messages.warning(request, 'ไม่พบ GitHub token กรุณาเชื่อมต่อบัญชี GitHub ใหม่อีกครั้ง')
        return redirect('socialaccount_connections')
    except Exception as e:
        messages.error(request, f'เกิดข้อผิดพลาด: {str(e)}')
        return redirect('projects:project_create')

@login_required
def github_repository_deploy(request, repo_id):
    """
    Deploy a project from GitHub repository
    """
    try:
        # Get GitHub account and token
        github_account = SocialAccount.objects.get(user=request.user, provider='github')
        token = SocialToken.objects.get(account=github_account)
        
        # Fetch repository details from GitHub API
        headers = {
            'Authorization': f'token {token.token}',
            'Accept': 'application/vnd.github.v3+json'
        }
        response = requests.get(f'https://api.github.com/repositories/{repo_id}', headers=headers)
        
        if response.status_code != 200:
            messages.error(request, f'ไม่สามารถดึงข้อมูล repository จาก GitHub ได้: {response.status_code}')
            return redirect('projects:project_create')
            
        repo_data = response.json()
        
        # Create new project
        project = Project()
        project.name = repo_data['name']
        project.description = repo_data['description'] or f"GitHub project: {repo_data['full_name']}" if repo_data.get('description') is not None else f"GitHub project: {repo_data['full_name']}"
        project.owner = request.user
        project.github_repo_url = repo_data['html_url']
        project.status = 'pending'
        
        # Assign a random port between 8001-8999
        used_ports = Project.objects.exclude(exposed_port__isnull=True).values_list('exposed_port', flat=True)
        available_ports = [p for p in range(8001, 9000) if p not in used_ports]
        if available_ports:
            project.exposed_port = random.choice(available_ports)
        else:
            messages.error(request, 'ไม่มีพอร์ตว่างสำหรับโปรเจกต์ใหม่')
            return redirect('projects:project_create')
        
        project.save()
        messages.success(request, f'โปรเจกต์ {project.name} ถูกสร้างเรียบร้อยแล้ว')
        return redirect('projects:project_detail', pk=project.pk)
        
    except SocialAccount.DoesNotExist:
        messages.warning(request, 'คุณยังไม่ได้เชื่อมต่อบัญชี GitHub กรุณาเชื่อมต่อบัญชีก่อน')
        return redirect('socialaccount_connections')
    except SocialToken.DoesNotExist:
        messages.warning(request, 'ไม่พบ GitHub token กรุณาเชื่อมต่อบัญชี GitHub ใหม่อีกครั้ง')
        return redirect('socialaccount_connections')
    except Exception as e:
        messages.error(request, f'เกิดข้อผิดพลาด: {str(e)}')
        return redirect('projects:project_create')

@login_required
def project_edit(request, pk):
    """
    Edit an existing project
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner
    if project.owner != request.user:
        messages.error(request, 'คุณไม่มีสิทธิ์แก้ไขโปรเจกต์นี้')
        return redirect('projects:project_list')
        
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request, f'โปรเจกต์ {project.name} ถูกอัปเดตเรียบร้อยแล้ว')
            return redirect('projects:project_detail', pk=project.pk)
    else:
        form = ProjectForm(instance=project)
    
    return render(request, 'projects/project_form.html', {
        'form': form,
        'title': f'แก้ไขโปรเจกต์: {project.name}',
        'project': project
    })

@login_required
def project_delete(request, pk):
    """
    Delete a project
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner
    if project.owner != request.user:
        messages.error(request, 'คุณไม่มีสิทธิ์ลบโปรเจกต์นี้')
        return redirect('projects:project_list')
        
    if request.method == 'POST':
        project_name = project.name
        project.delete()
        messages.success(request, f'โปรเจกต์ {project_name} ถูกลบเรียบร้อยแล้ว')
        return redirect('projects:project_list')
    
    return render(request, 'projects/project_confirm_delete.html', {'project': project})

@login_required
def project_deploy(request, pk):
    """
    Deploy a project
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner
    if project.owner != request.user:
        messages.error(request, 'คุณไม่มีสิทธิ์ deploy โปรเจกต์นี้')
        return redirect('projects:project_list')
    
    # Check if there's already a pending or in_progress deployment
    existing_deployment = Deployment.objects.filter(
        project=project, 
        status='in_progress'
    ).first()
    
    if existing_deployment:
        messages.warning(request, f'โปรเจกต์ {project.name} กำลังอยู่ในกระบวนการ deploy อยู่แล้ว')
        return redirect('projects:deployment_detail', pk=existing_deployment.pk)
    
    # Create a new deployment record
    deployment = Deployment.objects.create(
        project=project,
        status='in_progress',
        log='กำลังเริ่มต้นกระบวนการ deployment...'
    )
    
    # Update project status
    project.status = 'deploying'
    project.save()
    
    # In a real application, this would trigger an asynchronous task
    # For now, we'll just simulate a successful deployment
    
    messages.success(request, f'เริ่มต้น deploy โปรเจกต์ {project.name} แล้ว')
    return redirect('projects:deployment_detail', pk=deployment.pk)

@login_required
def deployment_list(request, pk):
    """
    List all deployments for a project
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner or if the project is public
    if project.owner != request.user and not project.is_public:
        messages.error(request, 'คุณไม่มีสิทธิ์เข้าถึงโปรเจกต์นี้')
        return redirect('projects:project_list')
        
    deployments = Deployment.objects.filter(project=project).order_by('-timestamp')
    return render(request, 'deployments/deployment_list.html', {'project': project, 'deployments': deployments})
        
@login_required
def deployment_set_production(request, project_pk, pk):
    """
    Set a deployment as the production deployment for a project
    """
    project = get_object_or_404(Project, pk=project_pk)
    deployment = get_object_or_404(Deployment, pk=pk, project=project)
    
    # Check if the user is the owner
    if project.owner != request.user:
        messages.error(request, 'คุณไม่มีสิทธิ์กำหนด production deployment สำหรับโปรเจกต์นี้')
        return redirect('projects:project_detail', pk=project_pk)
    
    # Check if deployment is successful
    if deployment.status != 'success':
        messages.error(request, 'สามารถกำหนด production deployment ได้เฉพาะ deployment ที่สำเร็จเท่านั้น')
        return redirect('projects:deployment_detail', pk=pk)
    
    # Reset production flag on all deployments for this project
    project_deployments = Deployment.objects.filter(project=project, is_production=True)
    for dep in project_deployments:
        dep.is_production = False
        dep.save()
    
    # Set this deployment as production
    deployment.is_production = True
    deployment.save()
    
    # Update project status
    project.status = 'running'
    project.save()
    
    messages.success(request, f'Deployment #{deployment.id} ถูกกำหนดเป็น production deployment สำหรับโปรเจกต์ {project.name} แล้ว')
    return redirect('projects:deployment_detail', pk=pk)

@login_required
def deployment_list(request, pk):
    """
    List all deployments for a project
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner or if the project is public
    if project.owner != request.user and not project.is_public:
        messages.error(request, 'คุณไม่มีสิทธิ์เข้าถึงโปรเจกต์นี้')
        return redirect('projects:project_list')
        
    deployments = Deployment.objects.filter(project=project).order_by('-timestamp')
    return render(request, 'deployments/deployment_list.html', {'project': project, 'deployments': deployments})

@login_required
def deployment_detail(request, pk):
    """
    View deployment details
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    project = deployment.project
    
    # Check if the user is the owner or if the project is public
    if project.owner != request.user and not project.is_public:
        messages.error(request, 'คุณไม่มีสิทธิ์เข้าถึงข้อมูล deployment นี้')
        return redirect('projects:project_list')
        
    return render(request, 'deployments/deployment_detail.html', {'deployment': deployment})

@login_required
def deployment_cancel(request, pk):
    """
    Cancel an in-progress deployment
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    
    # Check if the user is the owner
    if deployment.project.owner != request.user:
        messages.error(request, 'คุณไม่มีสิทธิ์ยกเลิก deployment นี้')
        return redirect('projects:project_detail', pk=deployment.project.pk)
    
    # Check if the deployment is in progress
    if deployment.status != 'in_progress':
        messages.error(request, 'สามารถยกเลิกได้เฉพาะ deployment ที่กำลังดำเนินการเท่านั้น')
        return redirect('projects:deployment_detail', pk=deployment.pk)
    
    # Update deployment status
    deployment.status = 'failed'
    deployment.log += '\nDeployment ถูกยกเลิกโดยผู้ใช้'
    deployment.save()
    
    # Update project status
    project = deployment.project
    project.status = 'stopped'
    project.save()
    
    messages.success(request, f'ยกเลิก deployment #{deployment.pk} สำเร็จแล้ว')
    return redirect('projects:deployment_detail', pk=deployment.pk)

@login_required
def deployment_create(request, pk):
    """
    Create a new deployment for a project
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the user is the owner
    if project.owner != request.user:
        messages.error(request, 'คุณไม่มีสิทธิ์สร้าง deployment สำหรับโปรเจกต์นี้')
        return redirect('projects:project_list')
    
    if request.method == 'POST':
        branch = request.POST.get('branch', 'main')
        commit_hash = request.POST.get('commit_hash', '')
        build_command = request.POST.get('build_command', 'npm run build')
        output_directory = request.POST.get('output_directory', 'build')
        auto_deploy = request.POST.get('auto_deploy') == 'on'
        
        # Update project with form data if provided
        if build_command:
            project.build_command = build_command
        if output_directory:
            project.output_dir = output_directory
        project.save()
        
        # Update project status
        project.status = 'deploying'
        project.save()
        
        # Start the deployment process
        deployment = deploy_project_from_github(project, request.user)
        
        messages.success(request, f'เริ่มต้น deploy โปรเจกต์ {project.name} แล้ว')
        return redirect('projects:deployment_detail', pk=deployment.pk)
    
    return render(request, 'deployments/deployment_create.html', {'project': project})

@login_required
def deployment_logs(request, project_pk, pk):
    """
    View deployment logs
    """
    deployment = get_object_or_404(Deployment, pk=pk, project__pk=project_pk)
    project = deployment.project
    
    # Check if the user is the owner or if the project is public
    if project.owner != request.user and not project.is_public:
        messages.error(request, 'คุณไม่มีสิทธิ์เข้าถึงข้อมูล deployment นี้')
        return redirect('projects:project_list')
        
    return render(request, 'deployments/deployment_logs.html', {'deployment': deployment, 'project': project})

@login_required
def deployment_logs_download(request, pk):
    """
    Download deployment logs
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    project = deployment.project
    
    # Check if the user is the owner or if the project is public
    if project.owner != request.user and not project.is_public:
        messages.error(request, 'คุณไม่มีสิทธิ์เข้าถึงข้อมูล deployment นี้')
        return redirect('projects:project_list')
    
    # Create a text file response
    response = HttpResponse(deployment.log, content_type='text/plain')
    response['Content-Disposition'] = f'attachment; filename="deployment-{deployment.pk}-logs.txt"'
    return response

def public_projects(request):
    """
    List all public projects
    """
    projects = Project.objects.filter(is_public=True).order_by('-created_at')
    return render(request, 'projects/public_projects.html', {'projects': projects})

def public_project_detail(request, pk):
    """
    View public project details
    """
    project = get_object_or_404(Project, pk=pk)
    
    # Check if the project is public
    if not project.is_public:
        messages.error(request, 'โปรเจกต์นี้ไม่ได้เปิดเป็นสาธารณะ')
        return redirect('projects:public_projects')
        
    deployments = project.deployments.order_by('-timestamp')[:5]
    latest_deployment = project.deployments.first()
    
    return render(request, 'projects/public_project_detail.html', {
        'project': project,
        'deployments': deployments,
        'latest_deployment': latest_deployment
    })

def public_deployment_detail(request, pk):
    """
    View public deployment details
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    project = deployment.project
    
    # Check if the project is public
    if not project.is_public:
        messages.error(request, 'โปรเจกต์นี้ไม่ได้เปิดเป็นสาธารณะ')
        return redirect('projects:public_projects')
        
    return render(request, 'deployments/public_deployment_detail.html', {'deployment': deployment})

def public_deployment_logs(request, pk):
    """
    View public deployment logs
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    project = deployment.project
    
    # Check if the project is public
    if not project.is_public:
        messages.error(request, 'โปรเจกต์นี้ไม่ได้เปิดเป็นสาธารณะ')
        return redirect('projects:public_projects')
        
    return render(request, 'deployments/public_deployment_logs.html', {'deployment': deployment})

def public_deployment_logs_download(request, pk):
    """
    Download public deployment logs
    """
    deployment = get_object_or_404(Deployment, pk=pk)
    project = deployment.project
    
    # Check if the project is public
    if not project.is_public:
        messages.error(request, 'โปรเจกต์นี้ไม่ได้เปิดเป็นสาธารณะ')
        return redirect('projects:public_projects')
    
    # Create a text file response
    response = HttpResponse(deployment.log, content_type='text/plain')
    response['Content-Disposition'] = f'attachment; filename="deployment-{deployment.pk}-logs.txt"'
    return response

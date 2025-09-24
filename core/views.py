from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from .models import Project, Deployment, EnvironmentVariable, UserProfile, SocialAccount
from django.utils import timezone
import requests
from django.conf import settings
import secrets
import json
from django.db.models import Q
import hmac
import hashlib
from django.views.decorators.csrf import csrf_exempt


def home(request):
    """หน้าแรกของเว็บไซต์"""
    return render(request, 'core/home.html')


def user_login(request):
    """หน้าเข้าสู่ระบบ"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            return redirect('core:dashboard')
        else:
            messages.error(request, 'Invalid username or password')
    
    return render(request, 'core/login.html')


def user_logout(request):
    """ออกจากระบบ"""
    logout(request)
    messages.success(request, 'You have been logged out successfully')
    return redirect('core:home')


def signup(request):
    """สมัครสมาชิก"""
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        password2 = request.POST.get('password2')
        
        # ตรวจสอบข้อมูล
        if not username or not email or not password:
            messages.error(request, 'Please fill in all fields')
            return render(request, 'core/signup.html')
        
        if password != password2:
            messages.error(request, 'Passwords do not match')
            return render(request, 'core/signup.html')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists')
            return render(request, 'core/signup.html')
        
        if User.objects.filter(email=email).exists():
            messages.error(request, 'Email already exists')
            return render(request, 'core/signup.html')
        
        # สร้างผู้ใช้ใหม่
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password
        )
        
        messages.success(request, 'Account created successfully! Please log in.')
        return redirect('core:user_login')
    
    return render(request, 'core/signup.html')

def github_login(request):
    """เริ่มกระบวนการ GitHub OAuth"""
    github_auth_url = 'https://github.com/login/oauth/authorize'
    params = {
        'client_id': settings.GITHUB_CLIENT_ID,
        'redirect_uri': settings.GITHUB_REDIRECT_URI,
        'scope': 'user:email repo',
        'state': secrets.token_urlsafe(32)
    }
    
    # เก็บ state ใน session เพื่อตรวจสอบความปลอดภัย
    request.session['github_oauth_state'] = params['state']
    
    auth_url = f"{github_auth_url}?client_id={params['client_id']}&redirect_uri={params['redirect_uri']}&scope={params['scope']}&state={params['state']}"
    return redirect(auth_url)

def github_callback(request):
    """รับ callback จาก GitHub OAuth"""
    code = request.GET.get('code')
    state = request.GET.get('state')
    
    # ตรวจสอบ state เพื่อความปลอดภัย
    if not state or state != request.session.get('github_oauth_state'):
        messages.error(request, 'Invalid OAuth state')
        return redirect('core:user_login')
    
    if not code:
        messages.error(request, 'No authorization code received')
        return redirect('core:user_login')
    
    # ขอ access token จาก GitHub
    token_url = 'https://github.com/login/oauth/access_token'
    token_data = {
        'client_id': settings.GITHUB_CLIENT_ID,
        'client_secret': settings.GITHUB_CLIENT_SECRET,
        'code': code,
        'redirect_uri': settings.GITHUB_REDIRECT_URI
    }
    
    headers = {'Accept': 'application/json'}
    token_response = requests.post(token_url, data=token_data, headers=headers)
    
    if token_response.status_code != 200:
        messages.error(request, 'Failed to get access token')
        return redirect('core:user_login')
    
    token_json = token_response.json()
    access_token = token_json.get('access_token')
    
    if not access_token:
        messages.error(request, 'No access token received')
        return redirect('core:user_login')
    
    # ขอข้อมูลผู้ใช้จาก GitHub
    user_url = 'https://api.github.com/user'
    user_headers = {
        'Authorization': f'token {access_token}',
        'Accept': 'application/json'
    }
    
    user_response = requests.get(user_url, headers=user_headers)
    
    if user_response.status_code != 200:
        messages.error(request, 'Failed to get user data')
        return redirect('core:user_login')
    
    user_data = user_response.json()
    github_username = user_data.get('login')
    github_email = user_data.get('email')
    avatar_url = user_data.get('avatar_url')
    
    if not github_username:
        messages.error(request, 'No GitHub username received')
        return redirect('core:user_login')
    
    # ใช้ email จาก GitHub หรือดึงจาก /user/emails ถ้าไม่มี
    if not github_email:
        emails_resp = requests.get('https://api.github.com/user/emails', headers=user_headers)
        if emails_resp.status_code == 200:
            try:
                emails = emails_resp.json()
                primary_email = next((e.get('email') for e in emails if e.get('primary')), None)
                if primary_email:
                    github_email = primary_email
            except Exception:
                pass
    
    if not github_email:
        github_email = f"{github_username}@github.local"
    
    # สร้างหรือหาผู้ใช้ที่มีอยู่
    try:
        user = User.objects.get(username=github_username)
        # อัปเดต email ถ้าจำเป็น
        if github_email and user.email != github_email:
            user.email = github_email
            user.save()
    except User.DoesNotExist:
        user = User.objects.create_user(
            username=github_username,
            email=github_email,
            password=secrets.token_urlsafe(32)  # รหัสผ่านสุ่ม
        )
    
    # อัปเดต/สร้างโปรไฟล์เพื่อเก็บ avatar และ github_username
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=user)
    profile.github_username = github_username
    if avatar_url:
        profile.avatar_url = avatar_url
    profile.save()
    
    # เก็บบัญชี Social (GitHub) พร้อมข้อมูล token และ avatar ใน extra_data
    SocialAccount.objects.update_or_create(
        user=user,
        provider='github',
        defaults={
            'uid': str(user_data.get('id') or github_username),
            'extra_data': {
                'access_token': access_token,
                'user_data': user_data,
                'avatar_url': avatar_url,
            }
        }
    )
    
    # เข้าสู่ระบบ
    login(request, user)
    messages.success(request, f'Logged in with GitHub as {github_username}')
    return redirect('core:dashboard')


@login_required
def dashboard(request):
    """แดชบอร์ดหลังจากเข้าสู่ระบบ"""
    projects = Project.objects.filter(owner=request.user).order_by('-created_at')
    
    # Deployments
    recent_deployments = Deployment.objects.filter(
        project__owner=request.user
    ).order_by('-timestamp')[:10]
    recent_projects = Project.objects.filter(owner=request.user).order_by('-updated_at')[:5]

    # แก้การนับ Active: เดิมนับทุก deployment ที่ success (เป็นประวัติย้อนหลัง)
    # ปรับให้นับจำนวนโปรเจกต์ที่กำลังรันอยู่จริง (status='running')
    active_projects = Project.objects.filter(owner=request.user, status='running')
    pending_deployments = Deployment.objects.filter(project__owner=request.user, status='in_progress')
    
    context = {
        'projects': projects,
        'active_projects': active_projects,
        'pending_deployments': pending_deployments,
        'recent_deployments': recent_deployments,
        'recent_projects': recent_projects,
    }
    return render(request, 'core/dashboard.html', context)


@login_required
def project_list(request):
    """รายการโปรเจกต์ทั้งหมด"""
    projects = Project.objects.filter(owner=request.user).order_by('-created_at')
    return render(request, 'core/project_list.html', {'projects': projects})


@login_required
def project_detail(request, project_id):
    """Project detail page showing info, env vars, and deployment history"""
    project = get_object_or_404(Project, id=project_id)
    if project.owner != request.user:
        return HttpResponseForbidden("You do not have access to this project")
    env_vars = EnvironmentVariable.objects.filter(project=project).order_by('key')
    deployments = Deployment.objects.filter(project=project).order_by('-timestamp')
    return render(request, 'core/project_detail.html', {
        'project': project,
        'env_vars': env_vars,
        'deployments': deployments,
    })

@login_required
def create_project(request):
    """Create a new project"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        github_repo_url = request.POST.get('github_repo_url', '').strip()
        exposed_port_raw = request.POST.get('exposed_port', '').strip()
        is_public = bool(request.POST.get('is_public'))
        dockerfile_path = request.POST.get('dockerfile_path', '').strip() or 'Dockerfile'
        build_command = request.POST.get('build_command', '').strip()
        run_command = request.POST.get('run_command', '').strip()
        env_vars_text = request.POST.get('env_vars', '')

        if not name or not github_repo_url:
            messages.error(request, 'Project name and GitHub repository URL are required.')
            return render(request, 'core/create_project.html', {
                'has_github_connected': SocialAccount.objects.filter(user=request.user, provider='github').exists(),
                'github_repos': []
            })

        # Parse env vars from KEY=VALUE lines into JSON dict
        env_dict = {}
        for line in env_vars_text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                env_dict[k.strip()] = v.strip()

        exposed_port = None
        if exposed_port_raw:
            try:
                exposed_port = int(exposed_port_raw)
            except ValueError:
                messages.warning(request, 'Invalid port provided. It will be auto-assigned.')
                exposed_port = None

        project = Project.objects.create(
            name=name,
            github_repo_url=github_repo_url,
            exposed_port=exposed_port,
            is_public=is_public,
            dockerfile_path=dockerfile_path,
            build_command=build_command,
            run_command=run_command,
            environment_variables=json.dumps(env_dict),
            owner=request.user,
        )
        messages.success(request, 'Project created successfully!')
        return redirect('core:project_detail', project_id=project.id)

    # GET: optionally fetch GitHub repos if connected
    github_repos = []
    has_github_connected = False
    try:
        acct = SocialAccount.objects.filter(user=request.user, provider='github').first()
        if acct and acct.extra_data.get('access_token'):
            has_github_connected = True
            token = acct.extra_data.get('access_token')
            headers = {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github+json'
            }
            try:
                resp = requests.get('https://api.github.com/user/repos?per_page=50&sort=updated', headers=headers, timeout=10)
                if resp.status_code == 200:
                    github_repos = resp.json()
            except Exception:
                github_repos = []
    except Exception:
        has_github_connected = False

    return render(request, 'core/create_project.html', {
        'has_github_connected': has_github_connected,
        'github_repos': github_repos,
    })

@login_required
def deploy_project(request, project_id):
    """Trigger deployment for a project and return JSON result"""
    if request.method != 'POST':
        return HttpResponseBadRequest('Invalid method')
    project = get_object_or_404(Project, id=project_id)
    if project.owner != request.user:
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    # create deployment record
    deployment = Deployment.objects.create(project=project, status='in_progress', log='Starting deployment...')

    success, message = project.deploy_with_docker()
    deployment.log = (deployment.log or '') + f"\n{message}"
    deployment.status = 'success' if success else 'failed'
    deployment.timestamp = timezone.now()
    deployment.save()

    return JsonResponse({'success': success, 'message': message})

@login_required
def deployment_detail(request, deployment_id):
    """Show details for a specific deployment"""
    deployment = get_object_or_404(Deployment, id=deployment_id)
    if deployment.project.owner != request.user:
        return HttpResponseForbidden("You do not have access to this deployment")

    recent_deployments = Deployment.objects.filter(project=deployment.project).order_by('-timestamp')[:10]
    return render(request, 'core/deployment_detail.html', {
        'deployment': deployment,
        'recent_deployments': recent_deployments,
    })

@login_required
def stop_project(request, project_id):
    """Stop a running project's container"""
    if request.method != 'POST':
        return HttpResponseBadRequest('Invalid method')
    project = get_object_or_404(Project, id=project_id)
    if project.owner != request.user:
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    success, message = project.stop_container()
    return JsonResponse({'success': success, 'message': message})

@login_required
def delete_project(request, project_id):
    """Delete a project and its resources"""
    if request.method != 'POST':
        return HttpResponseBadRequest('Invalid method')
    project = get_object_or_404(Project, id=project_id)
    if project.owner != request.user:
        return JsonResponse({'success': False, 'message': 'Not authorized'}, status=403)

    # Attempt to stop container if running
    try:
        project.stop_container()
    except Exception:
        pass

    project.delete()
    messages.success(request, 'Project deleted successfully')
    return JsonResponse({'success': True})

@login_required
def add_env_var(request, project_id):
    """Add an environment variable to a project"""
    project = get_object_or_404(Project, id=project_id)
    if project.owner != request.user:
        return HttpResponseForbidden("Not authorized")
    if request.method == 'POST':
        key = request.POST.get('key', '').strip()
        value = request.POST.get('value', '').strip()
        is_secret = bool(request.POST.get('is_secret'))
        if not key:
            messages.error(request, 'Key is required')
        else:
            EnvironmentVariable.objects.update_or_create(
                project=project,
                key=key,
                defaults={'value': value, 'is_secret': is_secret}
            )
            messages.success(request, 'Environment variable saved')
        return redirect('core:project_detail', project_id=project.id)
    return HttpResponseBadRequest('Invalid method')

@login_required
def delete_env_var(request, env_var_id):
    """Delete an environment variable"""
    env_var = get_object_or_404(EnvironmentVariable, id=env_var_id)
    if env_var.project.owner != request.user:
        return HttpResponseForbidden("Not authorized")
    if request.method == 'POST':
        project_id = env_var.project.id
        env_var.delete()
        messages.success(request, 'Environment variable deleted')
        return redirect('core:project_detail', project_id=project_id)
    return HttpResponseBadRequest('Invalid method')

@login_required
def profile(request):
    """View own profile"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)
    has_github_connected = SocialAccount.objects.filter(user=request.user, provider='github').exists()
    projects_count = Project.objects.filter(owner=request.user).count()
    deployments_count = Deployment.objects.filter(project__owner=request.user).count()
    return render(request, 'core/profile.html', {
        'user': request.user,
        'profile': profile,
        'has_github_connected': has_github_connected,
        'projects_count': projects_count,
        'deployments_count': deployments_count,
    })

@login_required
def edit_profile(request):
    """Edit own profile"""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        profile = UserProfile.objects.create(user=request.user)
    if request.method == 'POST':
        # Update core User fields
        user = request.user
        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name = request.POST.get('last_name', user.last_name)
        email = request.POST.get('email', '').strip()
        if email:
            user.email = email
        user.save()

        # Update Profile fields
        profile.bio = request.POST.get('bio', '').strip()
        profile.company = request.POST.get('company', '').strip()
        profile.location = request.POST.get('location', '').strip()
        profile.website = request.POST.get('website', '').strip()
        profile.github_username = request.POST.get('github_username', '').strip()
        profile.twitter_username = request.POST.get('twitter_username', '').strip()
        profile.linkedin_username = request.POST.get('linkedin_username', '').strip()

        # Only update avatar_url if a non-empty value is provided
        new_avatar_url = request.POST.get('avatar_url', '').strip()
        if new_avatar_url:
            profile.avatar_url = new_avatar_url
        # If no avatar_url provided, keep existing avatar_url (avoid overwriting with empty string)

        profile.save()
        messages.success(request, 'Profile updated successfully')
        return redirect('core:profile')
    return render(request, 'core/edit_profile.html', {'profile': profile})

@login_required
def change_password(request):
    """Change account password"""
    if request.method == 'POST':
        old_password = request.POST.get('old_password')
        new_password = request.POST.get('new_password')
        confirm_password = request.POST.get('confirm_password')
        if not request.user.check_password(old_password):
            messages.error(request, 'Current password is incorrect')
        elif not new_password:
            messages.error(request, 'New password cannot be empty')
        elif new_password != confirm_password:
            messages.error(request, 'Passwords do not match')
        else:
            request.user.set_password(new_password)
            request.user.save()
            messages.success(request, 'Password changed successfully. Please log in again.')
            return redirect('core:user_login')
    return render(request, 'core/change_password.html')

def explore_projects(request):
    """Public explore page showing only public projects"""
    q = request.GET.get('q', '').strip()
    projects = Project.objects.filter(is_public=True)
    if q:
        projects = projects.filter(Q(name__icontains=q))
    projects = projects.order_by('-updated_at')[:100]
    return render(request, 'core/explore_projects.html', {'projects': projects, 'q': q})

def public_profile(request, username):
    """Public profile page for a user, showing their public projects"""
    target_user = get_object_or_404(User, username=username)
    try:
        target_profile = target_user.profile
    except UserProfile.DoesNotExist:
        target_profile = UserProfile.objects.create(user=target_user)
    has_github_connected = SocialAccount.objects.filter(user=target_user, provider='github').exists()
    projects_count = Project.objects.filter(owner=target_user, is_public=True).count()
    deployments_count = Deployment.objects.filter(project__owner=target_user).count()
    public_projects = Project.objects.filter(owner=target_user, is_public=True).order_by('-updated_at')
    # Override 'user' in context to display target user's data in template
    return render(request, 'core/profile.html', {
        'user': target_user,
        'profile': target_profile,
        'has_github_connected': has_github_connected,
        'projects_count': projects_count,
        'deployments_count': deployments_count,
        'projects': public_projects,
    })

@csrf_exempt
def github_webhook(request, project_id):
    """Webhook endpoint to trigger deployments from GitHub push events"""
    project = get_object_or_404(Project, id=project_id)
    if not project.webhook_enabled:
        return JsonResponse({'success': False, 'message': 'Webhook disabled'}, status=403)

    # Validate signature if provided
    signature = request.headers.get('X-Hub-Signature-256')
    body = request.body
    if project.webhook_token and signature:
        try:
            sha_name, sig = signature.split('=')
            mac = hmac.new(project.webhook_token.encode('utf-8'), msg=body, digestmod=hashlib.sha256)
            expected = mac.hexdigest()
            if not hmac.compare_digest(sig, expected):
                return HttpResponseForbidden('Invalid signature')
        except Exception:
            return HttpResponseBadRequest('Malformed signature header')

    event = request.headers.get('X-GitHub-Event')
    if event != 'push':
        return JsonResponse({'success': True, 'message': 'Event ignored'})

    try:
        payload = json.loads(body.decode('utf-8'))
    except Exception:
        payload = {}

    # Branch filter
    ref = payload.get('ref', '')  # e.g., refs/heads/main
    branch = ref.split('/')[-1] if ref else ''
    if project.webhook_branch and branch and branch != project.webhook_branch:
        return JsonResponse({'success': True, 'message': f'Ignored branch {branch}'})

    # Create deployment record with commit hash
    commit_hash = ''
    try:
        commit_hash = payload.get('after', '')
    except Exception:
        pass

    deployment = Deployment.objects.create(project=project, status='in_progress', commit_hash=commit_hash, log='Triggered by GitHub webhook')
    success, message = project.deploy_with_docker()
    deployment.log = (deployment.log or '') + f"\n{message}"
    deployment.status = 'success' if success else 'failed'
    deployment.timestamp = timezone.now()
    deployment.save()

    return JsonResponse({'success': success, 'message': message})

def health_check(request):
    return JsonResponse({'status': 'ok', 'time': timezone.now().isoformat()})
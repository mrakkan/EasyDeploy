from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from allauth.socialaccount.models import SocialAccount
from .forms import CustomUserCreationForm, CustomAuthenticationForm, UserProfileForm
from projects.models import Project

def home(request):
    """
    Home page view
    """
    public_projects = Project.objects.filter(is_public=True).order_by('-created_at')[:6]
    return render(request, 'accounts/home.html', {'public_projects': public_projects})

def signup(request):
    """
    User registration view
    """
    if request.user.is_authenticated:
        return redirect('accounts:dashboard')
        
    if request.method == 'POST':
        form = CustomUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, 'บัญชีของคุณถูกสร้างเรียบร้อยแล้ว!')
            return redirect('accounts:dashboard')
    else:
        form = CustomUserCreationForm()
    
    return render(request, 'accounts/signup.html', {'form': form})

def login_view(request):
    """
    User login view
    """
    if request.user.is_authenticated:
        return redirect('accounts:dashboard')
        
    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.success(request, f'ยินดีต้อนรับกลับมา {username}!')
                return redirect('accounts:dashboard')
    else:
        form = CustomAuthenticationForm()
    
    return render(request, 'accounts/login.html', {'form': form})

def logout_view(request):
    """
    User logout view
    """
    logout(request)
    messages.success(request, 'คุณได้ออกจากระบบเรียบร้อยแล้ว')
    return redirect('accounts:home')

@login_required
def dashboard(request):
    """
    User dashboard view
    """
    user_projects = Project.objects.filter(owner=request.user).order_by('-created_at')
    projects_count = user_projects.count()
    
    # Count deployments and running projects
    deployments_count = 0
    running_count = 0
    
    for project in user_projects:
        deployments_count += project.deployments.count()
        if project.status == 'running':
            running_count += 1
    
    # Check if user has connected GitHub account
    has_github = SocialAccount.objects.filter(user=request.user, provider='github').exists()
    
    return render(request, 'accounts/dashboard.html', {
        'projects': user_projects,
        'has_github': has_github,
        'projects_count': projects_count,
        'deployments_count': deployments_count,
        'running_count': running_count
    })

@login_required
def profile(request):
    """
    User profile view with combined email and password change functionality
    """
    user = request.user
    password_form = None
    message = None
    message_type = None
    
    # Handle profile update (including email)
    if request.method == 'POST':
        if 'update_profile' in request.POST:
            # Handle profile update
            form = UserProfileForm(request.POST, instance=user)
            if form.is_valid():
                form.save()
                messages.success(request, 'โปรไฟล์ของคุณได้รับการอัพเดทเรียบร้อยแล้ว')
                return redirect('accounts:profile')
        elif 'change_password' in request.POST:
            # Handle password change
            old_password = request.POST.get('old_password')
            new_password1 = request.POST.get('new_password1')
            new_password2 = request.POST.get('new_password2')
            
            if not user.check_password(old_password):
                message = 'รหัสผ่านปัจจุบันไม่ถูกต้อง'
                message_type = 'danger'
            elif new_password1 != new_password2:
                message = 'รหัสผ่านใหม่ไม่ตรงกัน'
                message_type = 'danger'
            else:
                user.set_password(new_password1)
                user.save()
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                messages.success(request, 'รหัสผ่านของคุณได้รับการเปลี่ยนเรียบร้อยแล้ว')
                return redirect('accounts:profile')
        elif 'disconnect_github' in request.POST:
            # Handle GitHub disconnection
            account_id = request.POST.get('account_id')
            try:
                account = SocialAccount.objects.get(id=account_id, user=request.user)
                
                # ตรวจสอบว่าบัญชีนี้สร้างจาก GitHub หรือไม่
                # ถ้าบัญชีถูกสร้างจาก GitHub (ไม่มีรหัสผ่าน) จะไม่อนุญาตให้ถอนการเชื่อมต่อ
                if not request.user.has_usable_password():
                    messages.error(request, 'ไม่สามารถยกเลิกการเชื่อมต่อ GitHub ได้ เนื่องจากบัญชีนี้ถูกสร้างผ่าน GitHub')
                else:
                    # บัญชีที่สร้างปกติและเชื่อมต่อ GitHub ทีหลัง สามารถถอนการเชื่อมต่อได้
                    account.delete()
                    messages.success(request, 'บัญชี GitHub ได้ถูกยกเลิกการเชื่อมต่อเรียบร้อยแล้ว')
            except SocialAccount.DoesNotExist:
                messages.error(request, 'ไม่พบบัญชีที่ต้องการยกเลิกการเชื่อมต่อ')
            return redirect('accounts:profile')
    else:
        form = UserProfileForm(instance=user)
    
    # Check if user has connected GitHub account
    github_account = None
    try:
        github_account = SocialAccount.objects.get(user=request.user, provider='github')
    except SocialAccount.DoesNotExist:
        pass
    
    # Get user's projects and deployments count
    projects_count = Project.objects.filter(owner=user).count()
    deployments_count = 0  # You can implement this if you have a Deployment model
        
    return render(request, 'accounts/profile.html', {
        'form': form,
        'github_account': github_account,
        'projects_count': projects_count,
        'deployments_count': deployments_count,
        'message': message,
        'message_type': message_type
    })

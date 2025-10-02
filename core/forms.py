from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from .models import Project
from django.core.exceptions import ValidationError
from django.db.models import Q
import json


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    
    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if not email:
            raise ValidationError('Email is required')
        if User.objects.filter(email=email).exists():
            raise ValidationError('Email already exists')
        return email


class ProjectForm(forms.ModelForm):
    # Extra non-model input for env vars in KEY=VALUE lines
    env_vars = forms.CharField(required=False, widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5}))

    class Meta:
        model = Project
        fields = [
            'name', 'github_repo_url', 'exposed_port', 'is_public',
            'dockerfile_path', 'build_command', 'run_command',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'github_repo_url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://github.com/username/repository'}),
            'exposed_port': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 65535, 'placeholder': '3000'}),
            'is_public': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'dockerfile_path': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Dockerfile'}),
            'build_command': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'run_command': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_github_repo_url(self):
        url = (self.cleaned_data.get('github_repo_url') or '').strip()
        if not url:
            raise ValidationError('GitHub repository URL is required')
        if not (url.startswith('https://github.com/') or url.startswith('http://github.com/')):
            raise ValidationError('GitHub URL must start with https://github.com/')
        # Basic format check: must have at least owner/repo
        parts = url.replace('https://github.com/', '').replace('http://github.com/', '').split('/')
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValidationError('Invalid GitHub repository URL format')
        return url

    def clean_exposed_port(self):
        port = self.cleaned_data.get('exposed_port')
        if port is None:
            return None
        if port < 1 or port > 65535:
            raise ValidationError('Port must be between 1 and 65535')
        # Check uniqueness among user's projects
        qs = Project.objects.exclude(exposed_port__isnull=True).filter(exposed_port=port)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('This port is already used by another project')
        return port

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise ValidationError('Project name is required')
        # Optional: ensure uniqueness per owner if available in form
        if self.user:
            existing = Project.objects.filter(owner=self.user, name__iexact=name)
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            if existing.exists():
                raise ValidationError('You already have a project with this name')
        return name

    def clean_env_vars(self):
        raw = self.cleaned_data.get('env_vars', '')
        env_dict = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                raise ValidationError('Invalid environment variable format. Use KEY=VALUE per line.')
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValidationError('Environment variable key cannot be empty')
            env_dict[key] = value
        # store JSON as string into model field later in save()
        return json.dumps(env_dict)

    def save(self, commit=True):
        instance = super().save(commit=False)
        # attach owner if provided via form initialization
        if self.user and not instance.owner_id:
            instance.owner = self.user
        # If exposed_port not provided, auto-assign
        if instance.exposed_port is None:
            instance.exposed_port = instance.get_next_available_port()
        # Set environment_variables JSON from cleaned env_vars
        env_json = self.cleaned_data.get('env_vars', '')
        instance.environment_variables = env_json
        if commit:
            instance.save()
        return instance


# EnvironmentVariableForm removed; environment variables managed via Project.environment_variables JSON
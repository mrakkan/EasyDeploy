from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.template.defaultfilters import slugify
import uuid
import subprocess
import os
import json
import tempfile
import shutil
import time
import urllib.request


class Tag(models.Model):
    """Model for storing project tags"""
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    
    class Meta:
        db_table = 'tags'
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Project(models.Model):
    """Model for storing project information"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('deploying', 'Deploying'),
        ('running', 'Running'),
        ('failed', 'Failed'),
        ('stopped', 'Stopped'),
    ]
    
    name = models.CharField(max_length=100)
    github_repo_url = models.URLField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_public = models.BooleanField(default=False)
    exposed_port = models.IntegerField(null=True, blank=True)
    docker_container_id = models.CharField(max_length=100, blank=True)
    docker_image_name = models.CharField(max_length=100, blank=True)
    dockerfile_path = models.CharField(max_length=200, default='Dockerfile')
    build_command = models.CharField(max_length=200, blank=True, help_text="Custom build command if needed")
    run_command = models.CharField(max_length=200, blank=True, help_text="Custom run command if needed")
    environment_variables = models.TextField(blank=True, help_text="JSON format environment variables")
    # webhook settings
    webhook_enabled = models.BooleanField(default=False)
    webhook_token = models.CharField(max_length=64, blank=True)
    webhook_branch = models.CharField(max_length=100, default='main', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    tags = models.ManyToManyField(Tag, through='ProjectTag', related_name='projects')
    
    class Meta:
        db_table = 'projects'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def get_next_available_port(self):
        """Get next available port starting from 3000"""
        used_ports = Project.objects.exclude(exposed_port__isnull=True).values_list('exposed_port', flat=True)
        port = 3000
        while port in used_ports:
            port += 1
        return port
    
    def get_env_variables(self):
        """Parse environment variables from JSON"""
        try:
            return json.loads(self.environment_variables) if self.environment_variables else {}
        except json.JSONDecodeError:
            return {}
    
    def deploy_with_docker(self):
        """Deploy project using Docker with minimal-downtime (blue-green):
        - Clean old clone dir (avoid 'already exists') then git clone
        - Build image
        - Run a staging container on a temporary port and health-check it
        - Stop old container to free the canonical port, start new container with a temporary name
        - If success: remove old and rename new -> canonical
        - Always cleanup staging container and clone dir
        """
        try:
            self.status = 'deploying'
            self.save()

            # Ensure webhook token and exposed port
            if not self.webhook_token:
                self.webhook_token = uuid.uuid4().hex
                self.save()
            if not self.exposed_port:
                self.exposed_port = self.get_next_available_port()
                self.save()

            # Clone repository (clean target dir first to avoid 'already exists')
            repo_name = self.github_repo_url.split('/')[-1].replace('.git', '')
            tmp_dir = tempfile.gettempdir()
            clone_dir = os.path.join(tmp_dir, f"{repo_name}_{self.id}")
            # Try to clean target dir; if it still exists (Windows file locks), fall back to a unique dir
            if os.path.exists(clone_dir):
                shutil.rmtree(clone_dir, ignore_errors=True)
                if os.path.exists(clone_dir):
                    clone_dir = os.path.join(tmp_dir, f"{repo_name}_{self.id}_{uuid.uuid4().hex[:8]}")
            clone_result = subprocess.run(['git', 'clone', self.github_repo_url, clone_dir], capture_output=True, text=True)
            if clone_result.returncode != 0:
                raise Exception(f"Git clone failed: {clone_result.stderr}")

            # Check Dockerfile
            dockerfile_path = os.path.join(clone_dir, self.dockerfile_path)
            if not os.path.exists(dockerfile_path):
                raise Exception(f"Dockerfile not found at {self.dockerfile_path}")

            # Build Docker image
            image_name = f"{repo_name}_{self.id}".lower()
            self.docker_image_name = image_name
            self.save()
            build_cmd = ['docker', 'build', '-t', image_name, '-f', dockerfile_path, clone_dir]
            build_result = subprocess.run(build_cmd, capture_output=True, text=True)
            if build_result.returncode != 0:
                raise Exception(f"Docker build failed: {build_result.stderr}")

            # Prepare env
            env_vars = self.get_env_variables()

            # Run STAGING container on a temporary free port
            staging_port = self.get_next_available_port()
            if staging_port == self.exposed_port:
                staging_port += 1
            staging_name = f"{repo_name}_{self.id}_staging"
            # Remove any stale staging container with the same name
            subprocess.run(['docker', 'rm', '-f', staging_name], capture_output=True)

            staging_cmd = ['docker', 'run', '-d', '-p', f"{staging_port}:80"]
            for key, value in env_vars.items():
                staging_cmd.extend(['-e', f"{key}={value}"])
            staging_cmd.extend(['--name', staging_name])
            staging_cmd.append(image_name)
            if self.run_command:
                staging_cmd.extend(self.run_command.split())
            staging_result = subprocess.run(staging_cmd, capture_output=True, text=True)
            if staging_result.returncode != 0:
                raise Exception(f"Docker run (staging) failed: {staging_result.stderr}")
            staging_container_id = staging_result.stdout.strip()

            # Health check the staging container
            healthy = False
            staging_url = f"http://localhost:{staging_port}/"
            for _ in range(30):
                try:
                    with urllib.request.urlopen(staging_url, timeout=2):
                        healthy = True
                        break
                except Exception:
                    time.sleep(2)
            if not healthy:
                subprocess.run(['docker', 'rm', '-f', staging_container_id], capture_output=True)
                raise Exception(f"Staging container failed health check on {staging_url}")

            # Switch traffic with minimal downtime
            canonical_name = f"{repo_name}_{self.id}"
            new_name = f"{canonical_name}_new"
            old_id = (self.docker_container_id or '').strip()

            # Stop old to free the port (do NOT remove yet)
            if old_id:
                subprocess.run(['docker', 'stop', old_id], capture_output=True)

            # Ensure temp name not in use
            subprocess.run(['docker', 'rm', '-f', new_name], capture_output=True)

            final_cmd = ['docker', 'run', '-d', '-p', f"{self.exposed_port}:80"]
            for key, value in env_vars.items():
                final_cmd.extend(['-e', f"{key}={value}"])
            final_cmd.extend(['--name', new_name])
            final_cmd.append(image_name)
            if self.run_command:
                final_cmd.extend(self.run_command.split())
            final_result = subprocess.run(final_cmd, capture_output=True, text=True)
            if final_result.returncode != 0:
                # Rollback: cleanup new and start old back
                subprocess.run(['docker', 'rm', '-f', new_name], capture_output=True)
                if old_id:
                    subprocess.run(['docker', 'start', old_id], capture_output=True)
                subprocess.run(['docker', 'rm', '-f', staging_name], capture_output=True)
                raise Exception(f"Docker run (final) failed: {final_result.stderr}")

            # Promote new: remove old and rename
            new_container_id = final_result.stdout.strip()
            if old_id:
                subprocess.run(['docker', 'rm', old_id], capture_output=True)
            try:
                subprocess.run(['docker', 'rename', new_name, canonical_name], capture_output=True)
            except Exception:
                # If rename fails (name conflict), force remove name and retry once
                subprocess.run(['docker', 'rm', '-f', canonical_name], capture_output=True)
                subprocess.run(['docker', 'rename', new_name, canonical_name], capture_output=True)

            self.docker_container_id = new_container_id
            self.status = 'running'
            self.save()

            # Cleanup staging container and clone dir
            subprocess.run(['docker', 'rm', '-f', staging_name], capture_output=True)
            shutil.rmtree(clone_dir, ignore_errors=True)

            return True, f"Deployed successfully on port {self.exposed_port}"
        except Exception as e:
            self.status = 'failed'
            self.save()
            return False, str(e)
    
    def stop_container(self):
        """Stop Docker container"""
        try:
            # Derive canonical and temporary container names for this project
            repo_name = self.github_repo_url.split('/')[-1].replace('.git', '')
            canonical_name = f"{repo_name}_{self.id}"
            staging_name = f"{repo_name}_{self.id}_staging"
            new_name = f"{canonical_name}_new"

            # Remove by container ID if present (force remove to ensure cleanup)
            if self.docker_container_id:
                subprocess.run(['docker', 'rm', '-f', self.docker_container_id], capture_output=True)

            # Also attempt to remove by known names to avoid name conflicts on next deploy
            for name in [canonical_name, new_name, staging_name]:
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

            # Clear state
            self.docker_container_id = ''
            self.status = 'stopped'
            self.save()
            return True, "Container stopped and removed successfully"
        except Exception as e:
            return False, str(e)

    def check_container_status(self):
        """Check if container is actually running in Docker and update status"""
        if not self.docker_container_id:
            if self.status == 'running':
                self.status = 'stopped'
                self.save()
            return False
            
        try:
            # Check container status using docker inspect
            cmd = ['docker', 'inspect', '--format', '{{.State.Running}}', self.docker_container_id]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            # If command failed or container not running
            if result.returncode != 0 or result.stdout.strip().lower() != 'true':
                if self.status == 'running':
                    self.status = 'stopped'
                    self.docker_container_id = ''
                    self.save()
                return False
                
            # Container is running
            if self.status != 'running':
                self.status = 'running'
                self.save()
            return True
            
        except Exception:
            # On any error, assume container is not running
            if self.status == 'running':
                self.status = 'stopped'
                self.docker_container_id = ''
                self.save()
            return False
    
    def get_preview_url(self):
        """Return preview URL if container is running"""
        # First verify container is actually running
        is_running = self.check_container_status()
        if is_running and self.exposed_port:
            return f"http://localhost:{self.exposed_port}/"
        return ""


class ProjectTag(models.Model):
    """Many-to-Many relationship between projects and tags"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    
    class Meta:
        db_table = 'project_tags'
        unique_together = ['project', 'tag']


class Deployment(models.Model):
    """Model for storing deployment history"""
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('in_progress', 'In Progress'),
    ]
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='deployments')
    timestamp = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')
    log = models.TextField(blank=True)
    commit_hash = models.CharField(max_length=40, blank=True)
    
    class Meta:
        db_table = 'deployments'
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.project.name} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


# APIKey model removed (deprecated feature)


class UserProfile(models.Model):
    """Model for storing user profile information"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    bio = models.TextField(max_length=500, blank=True)
    company = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    github_username = models.CharField(max_length=100, blank=True)
    twitter_username = models.CharField(max_length=100, blank=True)
    linkedin_username = models.CharField(max_length=100, blank=True)
    avatar_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'user_profiles'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Profile - {self.user.username}"
    
    def get_full_name(self):
        """Get user's full name or username"""
        return self.user.get_full_name() or self.user.username
    
    def get_social_links(self):
        """Get all social media links"""
        links = {}
        if self.github_username:
            links['github'] = f"https://github.com/{self.github_username}"
        if self.twitter_username:
            links['twitter'] = f"https://twitter.com/{self.twitter_username}"
        if self.linkedin_username:
            links['linkedin'] = f"https://linkedin.com/in/{self.linkedin_username}"
        return links


class SocialAccount(models.Model):
    """Social accounts linked to a user (e.g., GitHub)"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='social_accounts')
    provider = models.CharField(max_length=50)  # e.g., 'github'
    uid = models.CharField(max_length=255)      # user id from provider
    extra_data = models.JSONField(default=dict) # arbitrary data from provider
    
    class Meta:
        db_table = 'social_accounts'
        unique_together = ['user', 'provider']
    
    def __str__(self):
        return f"{self.provider} - {self.user.username}"


class EnvironmentVariable(models.Model):
    """Model for storing environment variables"""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='env_vars')
    key = models.CharField(max_length=100)
    value = models.CharField(max_length=255)
    is_secret = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'environment_variables'
        unique_together = ['project', 'key']
    
    def __str__(self):
        return f"{self.project.name} - {self.key}"


class BuildCache(models.Model):
    """Model for storing build cache information"""
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='build_cache')
    cache_key = models.CharField(max_length=255)
    cache_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'build_caches'
    
    def __str__(self):
        return f"Build Cache - {self.project.name}"
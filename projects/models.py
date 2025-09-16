from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from accounts.models import User

class Tag(models.Model):
    """
    Model for project tags/technologies
    """
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    
    class Meta:
        db_table = 'tags'
        verbose_name = _('tag')
        verbose_name_plural = _('tags')
    
    def __str__(self):
        return self.name

class Project(models.Model):
    """
    Model for user projects to be deployed
    """
    STATUS_CHOICES = (
        ('pending', _('Pending')),
        ('deploying', _('Deploying')),
        ('running', _('Running')),
        ('failed', _('Failed')),
        ('stopped', _('Stopped')),
    )
    
    name = models.CharField(max_length=100)
    github_repo_url = models.URLField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_public = models.BooleanField(default=False)
    exposed_port = models.IntegerField(null=True, blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    tags = models.ManyToManyField(Tag, related_name='projects')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'projects'
        verbose_name = _('project')
        verbose_name_plural = _('projects')
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name

class Deployment(models.Model):
    """
    Model for project deployment history
    """
    STATUS_CHOICES = (
        ('success', _('Success')),
        ('failed', _('Failed')),
        ('in_progress', _('In Progress')),
    )
    
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='deployments')
    timestamp = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')
    log = models.TextField(blank=True)
    commit_hash = models.CharField(max_length=40, blank=True)
    preview_url = models.URLField(blank=True, null=True, help_text=_('URL where the deployed project can be accessed'))
    
    class Meta:
        db_table = 'deployments'
        verbose_name = _('deployment')
        verbose_name_plural = _('deployments')
        ordering = ['-timestamp']
    
    def __str__(self):
        return f"{self.project.name} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"
        
    @property
    def duration(self):
        """Return a formatted duration string if deployment is complete"""
        if self.status in ['success', 'failed'] and hasattr(self, 'updated_at'):
            delta = self.updated_at - self.timestamp
            minutes, seconds = divmod(delta.seconds, 60)
            return f"{minutes}m {seconds}s"
        return None

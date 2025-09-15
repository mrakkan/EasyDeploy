from django.contrib import admin
from .models import Project, Deployment, Tag

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'status', 'exposed_port', 'is_public', 'created_at')
    list_filter = ('status', 'is_public')
    search_fields = ('name', 'github_repo_url', 'owner__username')
    filter_horizontal = ('tags',)

@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = ('project', 'timestamp', 'status', 'commit_hash')
    list_filter = ('status',)
    search_fields = ('project__name', 'commit_hash')
    readonly_fields = ('timestamp',)

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

from django.urls import path
from . import views

app_name = 'projects'

urlpatterns = [
    path('', views.project_list, name='project_list'),
    path('create/', views.project_create, name='project_create'),
    path('<int:pk>/', views.project_detail, name='project_detail'),
    path('<int:pk>/edit/', views.project_edit, name='project_edit'),
    path('<int:pk>/delete/', views.project_delete, name='project_delete'),
    path('<int:pk>/deploy/', views.project_deploy, name='project_deploy'),
    path('<int:pk>/deployments/', views.deployment_list, name='deployment_list'),
    path('<int:pk>/deployment/create/', views.deployment_create, name='deployment_create'),
    path('deployment/<int:pk>/', views.deployment_detail, name='deployment_detail'),
    path('deployment/<int:pk>/cancel/', views.deployment_cancel, name='deployment_cancel'),
    path('<int:project_pk>/deployment/<int:pk>/set-production/', views.deployment_set_production, name='deployment_set_production'),
    path('<int:project_pk>/deployment/<int:pk>/logs/', views.deployment_logs, name='deployment_logs'),
    path('public/', views.public_projects, name='public_projects'),
    path('github/repositories/', views.github_repositories, name='github_repositories'),
    path('github/deploy/<str:repo_id>/', views.github_repository_deploy, name='github_repository_deploy'),
]
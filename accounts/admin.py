from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, SocialAccount

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_superuser')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    list_filter = ('is_superuser', 'is_active')

@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'provider', 'uid')
    search_fields = ('user__username', 'user__email', 'provider')
    list_filter = ('provider',)

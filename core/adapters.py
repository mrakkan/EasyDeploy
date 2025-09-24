from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import User
from django.contrib.auth import login
from django.shortcuts import redirect
from django.urls import reverse


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        # ตั้งค่าเพิ่มเติมสำหรับ user ที่สมัครผ่าน GitHub
        if sociallogin.account.provider == 'github':
            user.email = data.get('email', '')
            user.first_name = data.get('name', '')
        return user
    
    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        # ทำอะไรเพิ่มเติมหลังจากบันทึก user
        return user
    
    def get_login_redirect_url(self, request):
        """กำหนด URL ที่จะ redirect หลังจาก login สำเร็จ"""
        return reverse('core:dashboard')
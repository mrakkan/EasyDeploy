from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.urls import reverse

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter for social account authentication that ensures users are
    redirected to the dashboard after successful social login.
    """
    def get_login_redirect_url(self, request):
        """
        Override to ensure redirect to dashboard after social login.
        """
        return reverse('accounts:dashboard')
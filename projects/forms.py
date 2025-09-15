from django import forms
from .models import Project, Tag

class ProjectForm(forms.ModelForm):
    """
    Form for creating and updating projects
    """
    name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ชื่อโปรเจกต์'})
    )
    github_repo_url = forms.URLField(
        widget=forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'URL ของ GitHub Repository'})
    )
    is_public = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        required=False
    )

    class Meta:
        model = Project
        fields = ('name', 'github_repo_url', 'is_public', 'tags')

class TagForm(forms.ModelForm):
    """
    Form for creating and updating tags
    """
    name = forms.CharField(
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ชื่อแท็ก'})
    )

    class Meta:
        model = Tag
        fields = ('name',)
        
    def clean_name(self):
        name = self.cleaned_data['name']
        return name.lower()
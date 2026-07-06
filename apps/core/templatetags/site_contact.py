from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def support_email():
    return settings.SUPPORT_EMAIL

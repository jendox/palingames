from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def support_email():
    return settings.SUPPORT_EMAIL


@register.simple_tag
def support_telegram_url():
    return settings.SUPPORT_TELEGRAM_URL


@register.simple_tag
def support_instagram_url():
    return settings.SUPPORT_INSTAGRAM_URL

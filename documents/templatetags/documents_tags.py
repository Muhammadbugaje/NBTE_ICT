from django import template

register = template.Library()


@register.simple_tag
def scan_page_url():
    """The public Scan page (portal host, or qa.documents.verify_base_url)."""
    from documents.issue import scan_url

    return scan_url()

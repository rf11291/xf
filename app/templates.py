from __future__ import annotations
import json
from jinja2 import Environment, BaseLoader, select_autoescape

_jinja = Environment(
    loader=BaseLoader(),
    autoescape=select_autoescape(enabled_extensions=("html", "xml")),
)

def render_template(template_json: str, context: dict) -> tuple[str, str]:
    data = json.loads(template_json)
    subject_tpl = _jinja.from_string(data.get("subject", ""))
    html_tpl = _jinja.from_string(data.get("html", ""))
    return subject_tpl.render(**context), html_tpl.render(**context)

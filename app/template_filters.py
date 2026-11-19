from app.time_utils import CHINA_TZ, format_china_time


def china_time(value) -> str:
    return format_china_time(value)


def register_template_filters(app) -> None:
    app.jinja_env.filters["china_time"] = china_time

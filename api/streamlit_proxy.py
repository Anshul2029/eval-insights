"""
Flask blueprint that reverse-proxies Streamlit apps.
  /insights/* → localhost:8502
"""

import requests as _requests
from flask import Blueprint, request, Response

UPSTREAM_MAP = {
    "insights": "http://localhost:8502",
}

streamlit_proxy = Blueprint("streamlit_proxy", __name__)


def _proxy(prefix, path):
    upstream = UPSTREAM_MAP.get(prefix)
    if not upstream:
        return Response("Unknown proxy prefix", status=404)

    url = f"{upstream}/{prefix}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() not in ("host", "content-length")}

    resp = _requests.request(
        method=request.method,
        url=url,
        headers=headers,
        params=request.args,
        data=request.get_data(),
        cookies=request.cookies,
        allow_redirects=False,
        stream=True,
        timeout=120,
    )

    excluded = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    resp_headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]

    return Response(
        resp.iter_content(chunk_size=4096),
        status=resp.status_code,
        headers=resp_headers,
        content_type=resp.headers.get("content-type"),
    )


@streamlit_proxy.route("/insights/", defaults={"path": ""})
@streamlit_proxy.route("/insights/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def proxy_insights(path):
    return _proxy("insights", path)



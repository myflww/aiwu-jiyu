"""Explicit, reviewed image contributions made with the contributor's account."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from PIL import Image

from backend import custom_stickers, dpapi
from backend.paths import APP_HOME

REPOSITORY = "myflww/aiwu-jiyu"
REPO_URL = "https://github.com/" + REPOSITORY
LICENSE_ID = "CC-BY-NC-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc/4.0/"
PREFIX = "assets/plugins/astrbot_plugin_dafeiyu_stickers/community/submissions"
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
HEX = re.compile(r"[a-f0-9]{40}\Z")
_lock = threading.RLock()
_session = {"token": "", "login": ""}
_auth_generation = 0
_connecting = False
_job = {"running": False, "state": "idle", "message": "投稿前可先预览文件，再用自己的 GitHub 账号提交审核。", "pr_url": "", "fingerprint": ""}


class GitHubError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        message = {401: "GitHub 授权已失效，请重新连接。", 403: "GitHub 权限不足或请求受限，请检查公开仓库投稿权限，或导出投稿包。",
                   404: "GitHub 仓库或分支尚未就绪。", 422: "GitHub 暂未接受此次投稿，请查看已有投稿或稍后重试。"}.get(status, "GitHub 请求未完成，请稍后重试。")
        super().__init__(message)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("投稿授权请求不跟随跳转。")


class Client:
    def __init__(self, token: str):
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not path.startswith("/") or path.startswith("//") or ".." in path or "\r" in path or "\n" in path:
            raise ValueError("GitHub 请求路径无效。")
        request = Request("https://api.github.com" + path, method=method,
                          data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AiwuJiyu-Community-Stickers",
                                   "Content-Type": "application/json"})
        try:
            with build_opener(_NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("GitHub 返回的信息过大。")
            return json.loads(raw)
        except HTTPError as error:
            raise GitHubError(error.code) from None


def _auth_path() -> Path:
    directory = APP_HOME / "run"
    for path in (APP_HOME, directory):
        if path.exists() and (path.is_symlink() or getattr(path.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("GitHub 授权目录不可为链接。")
    path = directory / "sticker-github-auth.json"
    if path.is_symlink() or path.exists() and (path.stat().st_nlink != 1 or path.stat().st_size > 8192):
        raise ValueError("GitHub 授权文件不可为链接或异常文件。")
    return path


def _saved() -> dict:
    path = _auth_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as stream:
            raw = stream.read(8193)
        value = json.loads(raw) if len(raw) <= 8192 else None
        if (isinstance(value, dict) and LOGIN.fullmatch(value.get("login", ""))
                and isinstance(value.get("token_enc"), str) and value["token_enc"].startswith("dpapi:")):
            return value
    except (OSError, ValueError, TypeError):
        pass
    return {}


def auth_status() -> dict:
    with _lock:
        saved = _saved()
        login = _session["login"] or saved.get("login", "")
        return {"configured": bool(login), "login": login, "remembered": bool(saved), "connecting": _connecting, "repository": REPOSITORY}


def connect(body: dict, *, client_factory=Client) -> dict:
    global _auth_generation, _connecting
    token = body.get("token")
    remember = body.get("remember", False)
    if (not isinstance(token, str) or not 20 <= len(token) <= 512
            or not re.fullmatch(r"[A-Za-z0-9_]+", token) or type(remember) is not bool):
        raise ValueError("请填写有效的 GitHub 访问令牌。")
    with _lock:
        if _job["running"]:
            raise ValueError("投稿正在进行，完成后再更换 GitHub 授权。")
        _auth_generation += 1
        generation = _auth_generation
        _connecting = True
    try:
        return _finish_connect(token, remember, generation, client_factory)
    finally:
        with _lock:
            if generation == _auth_generation:
                _connecting = False


def _finish_connect(token, remember, generation, client_factory):
    global _connecting
    user = client_factory(token).request("GET", "/user")
    login = user.get("login")
    if not isinstance(login, str) or not LOGIN.fullmatch(login):
        raise ValueError("未能确认 GitHub 账号。")
    encrypted = dpapi.protect(token) if remember else ""
    if remember and not encrypted.startswith("dpapi:"):
        raise ValueError("Windows 加密暂不可用，请取消记住授权后重试。")
    with _lock:
        if generation != _auth_generation or _job["running"]:
            raise ValueError("GitHub 连接请求已取消，请重新连接。")
        path = _auth_path()
        if remember:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".github-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps({"login": login, "token_enc": encrypted}).encode("utf-8"))
                os.replace(temporary, path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)
        _session.update(token=token, login=login)
        _connecting = False
    return {"ok": True, **auth_status()}


def disconnect() -> dict:
    global _auth_generation, _connecting
    with _lock:
        if _job["running"]:
            raise ValueError("投稿正在进行，请等待完成后再断开授权。")
        _auth_generation += 1
        _connecting = False
        _session.update(token="", login="")
        _auth_path().unlink(missing_ok=True)
    return {"ok": True, **auth_status()}


def _credentials() -> tuple[str, str]:
    with _lock:
        saved = _saved()
        token = _session["token"] or dpapi.unprotect(saved.get("token_enc", ""))
        login = _session["login"] or saved.get("login", "")
    if not token or not LOGIN.fullmatch(login):
        raise ValueError("请先连接自己的 GitHub 账号，或选择导出投稿包。")
    return token, login


def _credit(value) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100 or any(ord(c) < 32 for c in value):
        raise ValueError("请填写 1–100 字的作者署名。")
    return value.strip()


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _plan(item_id: str, attribution: str) -> dict:
    exported = custom_stickers.export_item(item_id)
    meta = exported["metadata"]
    image = exported["image"]
    content = base64.b64decode(image["base64"], validate=True)
    if not content or hashlib.sha256(content).hexdigest() != image["sha256"]:
        raise ValueError("投稿图片校验未通过。")
    suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(image["mime"])
    if suffix is None:
        raise ValueError("投稿图片格式不受支持。")
    with Image.open(io.BytesIO(content)) as decoded:
        dimensions = {"width": decoded.width, "height": decoded.height, "frames": getattr(decoded, "n_frames", 1)}
    public = {key: meta.get(key, [] if key == "tags" else "") for key in
              ("title", "emotion", "tags", "description", "use_when", "avoid_when")}
    public.update(schema_version=1, attribution=_credit(attribution), license=LICENSE_ID, license_url=LICENSE_URL,
                  image={"file": "image." + suffix, "sha256": image["sha256"], "mime": image["mime"], **dimensions})
    # Dimensions are read from validated export metadata where available; no
    # local filenames, user paths, account IDs or private config are included.
    canonical = json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(canonical + b"\0" + content).hexdigest()
    prefix = PREFIX + "/" + fingerprint
    notice = (f"{public['title']}\nAuthor: {public['attribution']}\n\n"
              "Shared under Creative Commons Attribution-NonCommercial 4.0 International.\n"
              f"{LICENSE_URL}\nRetain the author's attribution and this notice when sharing.\n").encode("utf-8")
    files = {prefix + "/image." + suffix: content,
             prefix + "/metadata.json": json.dumps(public, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
             prefix + "/LICENSE.txt": notice}
    return {"fingerprint": fingerprint, "metadata": public, "files": files, "size": sum(map(len, files.values()))}


def preview(item_id: str, attribution: str) -> dict:
    plan = _plan(item_id, attribution)
    return {"ok": True, "fingerprint": plan["fingerprint"], "metadata": plan["metadata"],
            "files": list(plan["files"]), "size": plan["size"], "repository": REPOSITORY,
            "license": LICENSE_ID, "license_url": LICENSE_URL, "workflow": "pull_request_review"}


def export_zip(item_id: str, attribution: str, *, rights_confirmed: bool, fingerprint: str | None = None) -> tuple[bytes, str]:
    if rights_confirmed is not True:
        raise ValueError("请确认图片的公开分发权和投稿许可。")
    plan = _plan(item_id, attribution)
    if fingerprint is not None and fingerprint != plan["fingerprint"]:
        raise ValueError("素材或署名已变化，请重新预览后导出。")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in plan["files"].items():
            archive.writestr(path, content)
    return buffer.getvalue(), "AiwuJiyu-sticker-" + plan["fingerprint"][:12] + ".zip"


def _sha(value: str) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("GitHub 返回的提交标识不兼容。")
    return value


def _pr_url(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(re.escape(REPO_URL) + r"/pull/\d+", value):
        raise ValueError("GitHub 返回的投稿地址不兼容。")
    return value


def _submit(plan: dict, token: str, login: str, *, client_factory=Client) -> str:
    client = client_factory(token)
    if client.request("GET", "/user").get("login") != login:
        raise ValueError("GitHub 账号已变化，请重新预览投稿。")
    upstream = client.request("GET", "/repos/" + REPOSITORY)
    if upstream.get("private") is not False or upstream.get("full_name", "").casefold() != REPOSITORY.casefold():
        raise ValueError("投稿目标必须是爱屋及鱼的公开仓库。")
    base = upstream.get("default_branch", "main")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", base):
        raise ValueError("仓库默认分支不兼容。")
    branch = "contrib/stickers/" + plan["fingerprint"][:24]
    query = urlencode({"state": "all", "head": login + ":" + branch, "base": base})
    existing = client.request("GET", "/repos/" + REPOSITORY + "/pulls?" + query)
    if isinstance(existing, list) and existing:
        return _pr_url(existing[0]["html_url"])
    target = REPOSITORY if login.casefold() == "myflww" else login + "/aiwu-jiyu"
    if target != REPOSITORY:
        try:
            fork = client.request("GET", "/repos/" + target)
        except GitHubError as error:
            if error.status != 404:
                raise
            client.request("POST", "/repos/" + REPOSITORY + "/forks", {"name": "aiwu-jiyu", "default_branch_only": True})
            fork = None
            for _ in range(15):
                try:
                    fork = client.request("GET", "/repos/" + target)
                    break
                except GitHubError as pending:
                    if pending.status != 404:
                        raise
                    time.sleep(0.5)
            if fork is None:
                raise ValueError("GitHub 仍在创建你的 Fork，请稍后再点投稿。")
        if (fork.get("private") is not False or fork.get("fork") is not True
                or fork.get("parent", {}).get("full_name", "").casefold() != REPOSITORY.casefold()):
            raise ValueError("你的同名仓库不是爱屋及鱼 Fork；请导出投稿包，现有仓库不会被改写。")
    repo = "/repos/" + target
    head = _sha(client.request("GET", "/repos/" + REPOSITORY + "/git/ref/heads/" + base)["object"]["sha"])
    base_tree = _sha(client.request("GET", "/repos/" + REPOSITORY + "/git/commits/" + head)["tree"]["sha"])
    try:
        commit = _sha(client.request("GET", repo + "/git/ref/heads/" + branch)["object"]["sha"])
    except GitHubError as error:
        if error.status != 404:
            raise
        entries = []
        for path, content in plan["files"].items():
            blob = client.request("POST", repo + "/git/blobs", {"encoding": "base64", "content": base64.b64encode(content).decode("ascii")})
            if _sha(blob["sha"]) != _blob_sha(content):
                raise ValueError("GitHub 投稿文件校验未通过。")
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = _sha(client.request("POST", repo + "/git/trees", {"base_tree": base_tree, "tree": entries})["sha"])
        commit = _sha(client.request("POST", repo + "/git/commits", {"message": "Add contributed sticker", "tree": tree, "parents": [head]})["sha"])
        client.request("POST", repo + "/git/refs", {"ref": "refs/heads/" + branch, "sha": commit})
    # A retry may find an existing branch; never submit arbitrary user changes.
    comparison = client.request("GET", repo + "/compare/" + head + "..." + commit)
    files = comparison.get("files", [])
    expected = {path: _blob_sha(content) for path, content in plan["files"].items()}
    if (len(files) != len(expected) or {f.get("filename") for f in files} != set(expected)
            or any(f.get("status") != "added" or f.get("sha") != expected[f["filename"]] for f in files)):
        raise ValueError("投稿分支包含额外修改，已停止创建 PR；没有改动主分支。")
    body = ("新增一张用户主动投稿的表情。\n\n"
            f"- 公开许可：{LICENSE_ID}（{LICENSE_URL}）\n"
            f"- 内容指纹：`{plan['fingerprint']}`\n"
            "- 仅包含预览中的图片、说明与授权文件；请维护者审核后收录。\n")
    result = client.request("POST", "/repos/" + REPOSITORY + "/pulls", {
        "title": "[表情投稿] " + str(plan["metadata"]["title"])[:80], "head": login + ":" + branch,
        "base": base, "body": body, "maintainer_can_modify": False})
    return _pr_url(result["html_url"])


def job_status() -> dict:
    with _lock:
        return {"ok": True, **_job}


def start_submission(body: dict) -> dict:
    if body.get("rights_confirmed") is not True or body.get("license") != LICENSE_ID:
        raise ValueError("请确认公开投稿权与 CC BY-NC 4.0 许可。")
    plan = _plan(str(body.get("item_id") or ""), body.get("attribution"))
    if body.get("fingerprint") != plan["fingerprint"]:
        raise ValueError("素材或署名已经变化，请重新预览后投稿。")
    with _lock:
        if _job["running"]:
            raise ValueError("上一条投稿正在处理，请等待完成。")
        if _connecting:
            raise ValueError("GitHub 连接尚未完成，请稍后投稿。")
        # Claim the job under the same lock as reading its authorization.
        token, login = _credentials()
        _job.update(running=True, state="submitting", message="正在用你的账号建立投稿分支并提交 PR；不会直接改动主分支。", pr_url="", fingerprint=plan["fingerprint"])

    def work():
        try:
            url = _submit(plan, token, login)
            with _lock:
                _job.update(state="submitted", message="已提交 GitHub 投稿，请等待维护者审核。", pr_url=url)
        except (GitHubError, ValueError) as error:
            with _lock:
                _job.update(state="failed", message=str(error), pr_url="")
        except Exception:
            with _lock:
                _job.update(state="uncertain", message="暂时无法确认投稿结果；可再次查询。重试会先检查已有 PR，避免重复投稿。", pr_url="")
        finally:
            with _lock:
                _job["running"] = False
    threading.Thread(target=work, daemon=True, name="aiwu-sticker-contribution").start()
    return job_status()

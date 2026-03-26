import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


POSTMAN_PATH = "postman_collection/CRUD_for_yatube.postman_collection.json"


STATUS_NAME_TO_CODE = {
    "OK": 200,
    "Created": 201,
    "Unauthorized": 401,
    "Forbidden": 403,
    "Bad Request": 400,
    "Method Not Allowed": 405,
    "Not Found": 404,
    "No Content": 204,
}

COMMENT_ID_FOR_PERMISSION_TESTS = "comment_id_for_permission_tests"


def _substitute_template(s: str, variables: Dict[str, Any]) -> str:
    # Replace {{var}} with JSON-safe representation.
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in variables:
            raise KeyError(f"Missing variable: {name}")
        val = variables[name]
        if val is None:
            return "null"
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (int, float)):
            return str(val)
        return json.dumps(val)[1:-1]  # strip quotes for string placeholders

    return re.sub(r"{{\s*([a-zA-Z0-9_]+)\s*}}", repl, s)


def _extract_expected_status(node: Dict[str, Any]) -> Optional[int]:
    # Look into event/test scripts for `to.be.eql("<StatusName>")`
    for ev in node.get("event", []) or []:
        if ev.get("listen") != "test":
            continue
        script = ev.get("script") or {}
        exec_list = script.get("exec") or []
        for line in exec_list:
            m = re.search(r'to\.be\.eql\("([^"]+)"\)', line)
            if not m:
                continue
            status_name = m.group(1)
            if status_name in STATUS_NAME_TO_CODE:
                return STATUS_NAME_TO_CODE[status_name]
    return None


def _extract_json_body(
    node: Dict[str, Any],
    variables: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    body = (node.get("request") or {}).get("body") or {}
    if body.get("mode") != "raw":
        return None
    raw = body.get("raw")
    if not raw:
        return None
    raw_sub = _substitute_template(raw, variables)
    return json.loads(raw_sub)


def _extract_url(node: Dict[str, Any], variables: Dict[str, Any]) -> str:
    url = ((node.get("request") or {}).get("url") or {}).get("raw")
    if not url:
        raise ValueError("No url.raw in request")
    return _substitute_template(url, variables)


def _auth_headers_from_postman_auth(
    auth_obj: Dict[str, Any],
    variables: Dict[str, Any],
) -> Dict[str, str]:
    if not auth_obj:
        return {}
    if auth_obj.get("type") == "noauth":
        return {}
    if auth_obj.get("type") != "apikey":
        return {}

    apikey = auth_obj.get("apikey") or []
    header_name = None
    header_value_tmpl = None
    for item in apikey:
        if item.get("key") == "key":
            header_name = item.get("value")
        elif item.get("key") == "value":
            header_value_tmpl = item.get("value")
    if not header_name or header_value_tmpl is None:
        return {}

    # header_value_tmpl can contain {{userToken}} etc
    header_value = _substitute_template(str(header_value_tmpl), variables)
    return {str(header_name): header_value}


def _infer_username_from_auth(
    headers: Dict[str, str],
    vars_: Dict[str, Any],
) -> Optional[str]:
    # Authorization: Token <token>
    auth = headers.get("Authorization")
    if not auth:
        return None
    token = auth.split(" ", 1)[1] if " " in auth else auth
    if (
        "userToken" in vars_
        and vars_["userToken"] is not None
        and token == vars_["userToken"]
    ):
        return vars_.get("userUsername", "regular_user")
    if (
        "adminToken" in vars_
        and vars_["adminToken"] is not None
        and token == vars_["adminToken"]
    ):
        return vars_.get("adminUsername", "root")
    return None


def _assert_group(obj: Any, where: str) -> None:
    assert isinstance(obj, dict), f"{where}: expected object"
    for k in ["id", "title", "slug", "description"]:
        assert k in obj, f"{where}: missing key {k}"
    assert isinstance(obj["id"], int), f"{where}: id must be int"
    for k in ["title", "slug", "description"]:
        assert isinstance(obj[k], str), f"{where}: {k} must be str"


def _assert_post(obj: Any, where: str, username: Optional[str]) -> None:
    assert isinstance(obj, dict), f"{where}: expected object"
    for k in ["id", "author", "text", "pub_date", "image", "group"]:
        assert k in obj, f"{where}: missing key {k}"
    assert isinstance(obj["id"], int), f"{where}: id must be int"
    assert isinstance(obj["author"], str), f"{where}: author must be str"
    if username is not None:
        assert obj["author"] == username, f"{where}: author mismatch"
    assert isinstance(obj["text"], str), f"{where}: text must be str"
    assert isinstance(obj["pub_date"], str), f"{where}: pub_date must be str"
    assert (obj["image"] is None) or isinstance(obj["image"], str), (
        f"{where}: image must be str or null"
    )
    assert (obj["group"] is None) or isinstance(obj["group"], int), (
        f"{where}: group must be int or null"
    )


def _assert_comment(obj: Any, where: str, username: Optional[str]) -> None:
    assert isinstance(obj, dict), f"{where}: expected object"
    for k in ["id", "author", "text", "created", "post"]:
        assert k in obj, f"{where}: missing key {k}"
    assert isinstance(obj["id"], int), f"{where}: id must be int"
    assert isinstance(obj["author"], str), f"{where}: author must be str"
    if username is not None:
        assert obj["author"] == username, f"{where}: author mismatch"
    assert isinstance(obj["text"], str), f"{where}: text must be str"
    assert isinstance(obj["created"], str), f"{where}: created must be str"
    assert isinstance(obj["post"], int), f"{where}: post must be int"


def _collect_leaf_requests(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    if (
        isinstance(node, dict)
        and "item" in node
        and isinstance(node["item"], list)
    ):
        for ch in node["item"]:
            out.extend(_collect_leaf_requests(ch))
    if (
        isinstance(node, dict)
        and "request" in node
        and node.get("request") is not None
    ):
        out.append(node)
    return out


@dataclass
class RunResult:
    name: str
    method: str
    url: str
    expected_status: Optional[int]
    actual_status: int
    ok: bool
    error: Optional[str]


def run() -> int:  # noqa: C901
    with open(POSTMAN_PATH, "r", encoding="utf-8") as f:
        collection = json.load(f)

    base_url = "http://127.0.0.1:8000/api/v1"
    vars_: Dict[str, Any] = {}

    # We also need inherited auth to match Postman.
    # Execute requests in the same order as in the Postman JSON.
    # Keep current auth by inspecting an ancestor `auth` field.
    failures: List[RunResult] = []
    successes = 0

    def walk(
        items: List[Dict[str, Any]],
        inherited_auth: Optional[Dict[str, Any]],
    ) -> None:
        nonlocal failures, successes, vars_
        for node in items:
            if not isinstance(node, dict):
                continue
            # Update inherited auth if node has auth
            auth_here = node.get("auth", None)
            if auth_here is not None:
                inherited = auth_here
            else:
                inherited = inherited_auth

            if "item" in node:
                walk(node["item"], inherited)

            if "request" in node:
                req = node["request"]
                method = (req.get("method") or "GET").upper()
                try:
                    expected_status = _extract_expected_status(node)
                    url = _extract_url(node, vars_)
                    headers = {}
                    # Keep explicit headers, but don't override Authorization.
                    for h in req.get("header") or []:
                        if (
                            h.get("key")
                            and (h.get("disabled") is not True)
                        ):
                            headers[str(h["key"])] = str(h.get("value", ""))

                    # Postman auth can be defined on node or on request.
                    # Prefer request-level auth.
                    auth_for_request = req.get("auth") or inherited or {}
                    headers.update(
                        _auth_headers_from_postman_auth(
                            auth_for_request or {}, vars_
                        )
                    )

                    if method in ("POST", "PUT", "PATCH"):
                        json_body = _extract_json_body(node, vars_)
                    else:
                        json_body = None

                    s = requests.Session()
                    s.trust_env = False
                    r = s.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        timeout=30,
                    )

                    actual = r.status_code
                    ok = expected_status is None or actual == expected_status
                    err = None

                    # Check basic response shape for common success cases.
                    # Skip full Postman jsonSchema validation.
                    username = _infer_username_from_auth(headers, vars_)
                    if ok and actual in (200, 201):
                        data = r.json()
                        path = url.replace(base_url, "")

                        if path.startswith("/api-token-auth"):
                            assert (
                                "token" in data
                                and isinstance(data["token"], str)
                            ), "auth: token missing"
                        elif path == "/groups/" and method == "GET":
                            assert (
                                isinstance(data, list) and data
                            ), "groups list must be non-empty"
                            _assert_group(
                                data[0],
                                node.get("name", "groups list")[0:80],
                            )
                        elif re.match(r"^/groups/\\d+/$", path):
                            _assert_group(data, node.get("name", "group"))
                        elif path == "/posts/" and method == "GET":
                            assert (
                                isinstance(data, list) and data
                            ), "posts list must be non-empty"
                            _assert_post(
                                data[0],
                                node.get("name", "posts list")[0:80],
                                username,
                            )
                        elif path == "/posts/" and method in (
                            "POST",
                            "PUT",
                            "PATCH",
                        ):
                            _assert_post(
                                data,
                                node.get("name", "post create/update")[0:80],
                                username,
                            )
                        elif re.match(r"^/posts/\\d+/$", path):
                            _assert_post(
                                data,
                                node.get("name", "post detail")[0:80],
                                username,
                            )
                        elif (
                            re.match(r"^/posts/\\d+/comments/$", path)
                            and method == "GET"
                        ):
                            assert (
                                isinstance(data, list) and data
                            ), "comments list must be non-empty"
                            _assert_comment(
                                data[0],
                                node.get("name", "comments list")[0:80],
                                username,
                            )
                        elif (
                            re.match(r"^/posts/\\d+/comments/$", path)
                            and method in ("POST", "PUT", "PATCH")
                        ):
                            _assert_comment(
                                data,
                                node.get("name", "comment")[:80],
                                username,
                            )
                        elif re.match(r"^/posts/\\d+/comments/\\d+/$", path):
                            _assert_comment(
                                data,
                                node.get("name", "comment detail")[0:80],
                                username,
                            )
                    elif ok and actual == 204:
                        # no content: ok
                        pass

                    if ok:
                        # Update variables based on request name.
                        name = node.get("name", "")
                        if actual in (200, 201) and node.get("name"):
                            data = r.json() if actual in (200, 201) else None
                            obj_id = data["id"]
                            if name == "get_token_for_regular_user // No Auth":
                                vars_["userToken"] = data["token"]
                                vars_["userUsername"] = "regular_user"
                            elif name == "get_token_for_admin // No Auth":
                                vars_["adminToken"] = data["token"]
                                vars_["adminUsername"] = "root"
                            elif name == "get_group_list // User":
                                vars_["group_id"] = data[0]["id"]
                            elif name in (
                                "create_post_without_group // User",
                                "update_post_with_patch_request // User",
                                "update_post_with_put_request // User",
                            ):
                                vars_["post_without_group"] = data["id"]
                            elif name == "create_post_with_group // User":
                                vars_["post_with_group"] = data["id"]
                            elif name == "create_comment // User":
                                vars_["comment_id"] = data["id"]
                            elif name == (
                                "create_comment_for_permission_tests // User"
                            ):
                                vars_[COMMENT_ID_FOR_PERMISSION_TESTS] = obj_id
                            elif name == (
                                "create_post_from_another_author // Admin"
                            ):
                                vars_["negative_test_post"] = data["id"]
                        successes += 1
                    else:
                        err = err or (
                            "Unexpected status: "
                            f"expected={expected_status}, actual={actual}, "
                            f"body={r.text[:200]}"
                        )

                    if not ok:
                        failures.append(
                            RunResult(
                                name=node.get("name", ""),
                                method=method,
                                url=url,
                                expected_status=expected_status,
                                actual_status=actual,
                                ok=False,
                                error=err,
                            )
                        )

                except Exception as e:
                    # Missing variables or parsing errors.
                    failures.append(
                        RunResult(
                            name=node.get("name", ""),
                            method=node.get("request", {}).get("method", ""),
                            url="(failed)",
                            expected_status=None,
                            actual_status=-1,
                            ok=False,
                            error=str(e),
                        )
                    )

    top_items = collection.get("item") or []
    walk(top_items, collection.get("auth", None))

    # Print results
    print(f"Checks finished. ok={successes}, failed={len(failures)}")
    for f in failures:
        print(
            f"- FAIL: {f.name} [{f.method} {f.url}] "
            f"expected_status={f.expected_status} "
            f"actual={f.actual_status} err={f.error}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())

import os
import stat
import subprocess
import yaml
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
DEPLOY_ENV_DIR = "/home/vietnh/apps/mini-hermes/envs"

REQUIRED_BACKEND_KEYS = {
    "POSTGRES_PASSWORD",
    "DATABASE_URL",
    "REDIS_URL",
    "STORAGE_PROVIDER",
    "S3_ENDPOINT_URL",
    "S3_BUCKET_NAME",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_ADDRESSING_STYLE",
    "WEBUI_SECRET_KEY",
    "MH_TOKEN_ENCRYPTION_KEY",
    "MH_CONNECTOR_SECRET",
    "MH_CONNECTOR_URL",
    "MH_SANDBOX_URL",
    "MH_SANDBOX_TOKEN",
    "CORS_ALLOW_ORIGIN",
}

REQUIRED_FRONTEND_KEYS = {
    "VITE_WEBUI_BACKEND_URL",
    "WEBUI_BACKEND_URL",
}


def parse_env_file(path: str) -> dict:
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def test_env_example_templates_conform():
    """Verify repo env templates exist and contain all required contract keys."""
    fe_prod = parse_env_file(os.path.join(REPO_ROOT, "envs/frontend.prod.env.example"))
    fe_dev = parse_env_file(os.path.join(REPO_ROOT, "envs/frontend.dev.env.example"))
    be_prod = parse_env_file(os.path.join(REPO_ROOT, "envs/backend.prod.env.example"))
    be_dev = parse_env_file(os.path.join(REPO_ROOT, "envs/backend.dev.env.example"))

    for k in REQUIRED_FRONTEND_KEYS:
        assert k in fe_prod, f"Missing {k} in fe_prod"
        assert k in fe_dev, f"Missing {k} in fe_dev"

    for k in REQUIRED_BACKEND_KEYS:
        assert k in be_prod, f"Missing {k} in be_prod"
        assert k in be_dev, f"Missing {k} in be_dev"


def test_real_deployment_envs_isolation():
    """Verify host deployment env files are restricted to 0600 and secrets are strictly isolated."""
    if not os.path.exists(DEPLOY_ENV_DIR):
        pytest.skip("Deploy env directory not on this host runner.")

    prod_env = parse_env_file(os.path.join(DEPLOY_ENV_DIR, "backend.prod.env"))
    dev_env = parse_env_file(os.path.join(DEPLOY_ENV_DIR, "backend.dev.env"))

    # Permission check: 0600
    for name in ["backend.prod.env", "backend.dev.env", "frontend.prod.env", "frontend.dev.env"]:
        p = os.path.join(DEPLOY_ENV_DIR, name)
        mode = oct(stat.S_IMODE(os.stat(p).st_mode))
        assert mode == "0o600", f"Insecure permission {mode} on {p}"

    # Secret isolation check: Prod and Dev secrets MUST NEVER be identical
    sensitive_keys = [
        "POSTGRES_PASSWORD",
        "S3_SECRET_ACCESS_KEY",
        "WEBUI_SECRET_KEY",
        "MH_TOKEN_ENCRYPTION_KEY",
        "MH_CONNECTOR_SECRET",
        "MH_SANDBOX_TOKEN",
    ]
    for key in sensitive_keys:
        assert prod_env.get(key) != dev_env.get(key), (
            f"Security risk: {key} must not be shared between prod and dev!"
        )


def test_compose_yaml_network_isolation_and_security():
    """Verify Compose specifications adhere to security and networking isolation contracts."""
    compose_path = os.path.join(REPO_ROOT, "compose.yaml")
    with open(compose_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    services = doc.get("services", {})
    networks = doc.get("networks", {})

    # 1. Sandbox network must be internal
    assert networks.get("sandbox", {}).get("internal") is True, "Sandbox network must be strictly internal"

    # 2. proxy-nw must be external
    assert networks.get("proxy-nw", {}).get("external") is True, "proxy-nw must be external"

    # 3. Database & storage isolation: postgres and redis must NEVER be attached to proxy-nw
    assert "proxy-nw" not in services.get("postgres", {}).get("networks", []), "postgres must not be on proxy-nw"
    assert "proxy-nw" not in services.get("redis", {}).get("networks", []), "redis must not be on proxy-nw"

    # 4. App container healthcheck must be configured
    assert "healthcheck" in services.get("app", {}), "App service must declare a healthcheck"
    assert services.get("app", {}).get("restart") == "unless-stopped", "App must restart unless-stopped"


def test_deploy_and_verify_script_valid():
    """Verify deploy script syntax and executability."""
    script_path = os.path.join(REPO_ROOT, "scripts/deploy_and_verify.sh")
    assert os.path.isfile(script_path), "deploy_and_verify.sh must exist"
    assert os.access(script_path, os.X_OK), "deploy_and_verify.sh must be executable"

    res = subprocess.run(["sh", "-n", script_path], capture_output=True, text=True)
    assert res.returncode == 0, f"Script syntax error in sh: {res.stderr}"


def test_gitlab_ci_yaml_contract_and_domains():
    """Verify .gitlab-ci.yml syntax and target domain contracts."""
    ci_path = os.path.join(REPO_ROOT, ".gitlab-ci.yml")
    with open(ci_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)

    deploy_dev = doc.get("deploy_dev", {})
    deploy_prod = doc.get("deploy_prod", {})

    assert deploy_dev.get("environment", {}).get("url") == "https://dev-mini-hermes.ducnq.io.vn"
    assert deploy_prod.get("environment", {}).get("url") == "https://mini-hermes.ducnq.io.vn"

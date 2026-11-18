import base64
import os
import secrets
import sys
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

app = typer.Typer()
mfa_app = typer.Typer(help='Manage multi-factor authentication.')
app.add_typer(mfa_app, name='mfa')

KEY_FILE = Path.cwd() / '.weai_secret_key'
DEFAULT_SECRET_KEY_LENGTH = 24


def version_callback(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"WeAI version: {version('weai')}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[bool | None, typer.Option('--version', callback=version_callback, is_eager=True)] = None,
):
    pass


@app.command()
def serve(
    host: str = '0.0.0.0',
    port: int = 8080,
):
    os.environ['FROM_INIT_PY'] = 'true'
    if os.getenv('WEAI_SECRET_KEY') is None:
        typer.echo('Loading WEAI_SECRET_KEY from file, not provided as an environment variable.')
        if not KEY_FILE.exists():
            key_length = int(os.getenv('WEAI_SECRET_KEY_LENGTH', DEFAULT_SECRET_KEY_LENGTH))
            if key_length < 1:
                raise ValueError('WEAI_SECRET_KEY_LENGTH must be a positive integer')
            typer.echo(f'Generating a new secret key and saving it to {KEY_FILE}')
            KEY_FILE.write_bytes(base64.b64encode(secrets.token_bytes(key_length)))
        typer.echo(f'Loading WEAI_SECRET_KEY from {KEY_FILE}')
        os.environ['WEAI_SECRET_KEY'] = KEY_FILE.read_text()

    if os.getenv('USE_CUDA_DOCKER', 'false') == 'true':
        typer.echo('CUDA is enabled, appending LD_LIBRARY_PATH to include torch/cudnn & cublas libraries.')
        LD_LIBRARY_PATH = os.getenv('LD_LIBRARY_PATH', '').split(':')
        os.environ['LD_LIBRARY_PATH'] = ':'.join(
            LD_LIBRARY_PATH
            + [
                '/usr/local/lib/python3.12/site-packages/torch/lib',
                '/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib',
            ]
        )
        try:
            import torch

            assert torch.cuda.is_available(), 'CUDA not available'
            typer.echo('CUDA seems to be working')
        except Exception as e:
            typer.echo(
                'Error when testing CUDA but USE_CUDA_DOCKER is true. '
                'Resetting USE_CUDA_DOCKER to false and removing '
                f'LD_LIBRARY_PATH modifications: {e}'
            )
            os.environ['USE_CUDA_DOCKER'] = 'false'
            os.environ['LD_LIBRARY_PATH'] = ':'.join(LD_LIBRARY_PATH)

    import weai.main  # noqa: F401
    from weai.env import UVICORN_WORKERS, UVICORN_WS_PER_MESSAGE_DEFLATE

    # On Windows, uvicorn's default loop factory hardcodes ProactorEventLoop,
    # which is incompatible with psycopg v3 async.  Setting loop='none' lets
    # asyncio.run() respect the WindowsSelectorEventLoopPolicy set in db.py.
    loop = 'none' if sys.platform == 'win32' else 'auto'

    uvicorn.run(
        'weai.main:app',
        host=host,
        port=port,
        forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS', '*'),
        workers=UVICORN_WORKERS,
        ws_per_message_deflate=UVICORN_WS_PER_MESSAGE_DEFLATE,
        loop=loop,
    )


@app.command()
def dev(
    host: str = '0.0.0.0',
    port: int = 8080,
    reload: bool = True,
):
    from weai.env import UVICORN_WS_PER_MESSAGE_DEFLATE

    uvicorn.run(
        'weai.main:app',
        host=host,
        port=port,
        reload=reload,
        forwarded_allow_ips=os.getenv('FORWARDED_ALLOW_IPS', '*'),
        ws_per_message_deflate=UVICORN_WS_PER_MESSAGE_DEFLATE,
    )


@mfa_app.command()
def reset(email: str, reason: Annotated[str, typer.Option('--reason')]):
    """Issue a one-time recovery ticket after the operator verifies the user's identity."""
    import asyncio

    if not os.getenv('WEAI_SECRET_KEY'):
        if not KEY_FILE.exists():
            raise typer.BadParameter(
                'Provide the existing WEAI_SECRET_KEY or run in the directory containing .weai_secret_key.'
            )
        os.environ['WEAI_SECRET_KEY'] = KEY_FILE.read_text()
    from weai.utils.mfa import reset_mfa

    try:
        ticket = asyncio.run(reset_mfa(email, reason))
    except Exception as error:
        typer.echo(f'Reset failed: {error}', err=True)
        raise typer.Exit(1) from None
    typer.echo('Recovery token (expires in 30 minutes; deliver securely to the verified user):')
    typer.echo(ticket)


if __name__ == '__main__':
    app()

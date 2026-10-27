"""Offline evidence CLI, without settings, database access or network calls."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from acs.cwmp.compatibility.evidence_package import build_package
from acs.cwmp.soap.inform import MAX_BODY
from acs.domain.operations.evidence import InvalidEvidence
from acs.infrastructure.storage.evidence_package import (
    load_package,
    publish_package,
    read_bounded,
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Criar e verificar pacote parcial de Inform offline."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create", help="Reconstruir XML anonimizado e empacotar"
    )
    create.add_argument("--input", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser(
        "verify", help="Verificar formato, conteúdo e integridade"
    )
    verify.add_argument("package", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            package = build_package(read_bounded(args.input, MAX_BODY))
            publish_package(args.output, package)
            manifest = package.manifest
        else:
            manifest = load_package(args.package)
    except FileExistsError:
        raise SystemExit("evidence_already_exists") from None
    except (InvalidEvidence, OSError):
        raise SystemExit("evidence_invalid_or_unavailable") from None
    print(
        json.dumps(
            {
                "status": "created" if args.command == "create" else "verified",
                "format_version": manifest.format_version,
                "partial": manifest.partial,
            }
        )
    )


if __name__ == "__main__":
    main()

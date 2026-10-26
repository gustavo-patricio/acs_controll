#!/usr/bin/env bash
# Local CA and server certificate for the laboratory LAN. Run inside WSL.
set -euo pipefail
umask 077

if [[ $# != 1 || ! $1 =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "Uso: bash scripts/create_lab_tls.sh IPV4_DO_ACS" >&2
    exit 1
fi
acs_ip=$1
IFS=. read -r -a octets <<< "$acs_ip"
for octet in "${octets[@]}"; do
    if (( 10#$octet > 255 )) || [[ ${#octet} -gt 1 && $octet == 0* ]]; then
        echo "IPv4 invalido ou com zeros a esquerda." >&2
        exit 1
    fi
done
command -v openssl >/dev/null
backend_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
tls_dir="${ACS_LAB_TLS_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/acs/lab-tls}"
mkdir -p -- "$tls_dir"
tls_dir=$(cd -- "$tls_dir" && pwd)
chmod 700 "$tls_dir"
if [[ $(stat -c %a "$tls_dir") != 700 ]]; then
    echo "Use ACS_LAB_TLS_DIR no filesystem Linux, com permissoes 700 (nao /mnt/c ou /mnt/d)." >&2
    exit 1
fi
mkdir -p "$backend_dir/.tls"
link="$backend_dir/.tls/mirrored"
if [[ -e $link || -L $link ]]; then
    if [[ ! -L $link || $(readlink "$link") != "$tls_dir" ]]; then
        echo ".tls/mirrored ja existe com outro destino; nenhum certificado foi alterado." >&2
        exit 1
    fi
fi

work_dir=$(mktemp -d "$tls_dir/.issuance.XXXXXXXX")
trap 'rm -rf -- "$work_dir"' EXIT
if [[ -e $tls_dir/ca.key || -e $tls_dir/ca.crt ]]; then
    if [[ ! -f $tls_dir/ca.key || ! -f $tls_dir/ca.crt ]]; then
        echo "CA incompleta; restaure o par ca.key/ca.crt antes de continuar." >&2
        exit 1
    fi
    openssl x509 -in "$tls_dir/ca.crt" -checkend 31536000 -noout
else
    openssl req -x509 -newkey rsa:3072 -noenc -sha256 -days 3650 \
        -keyout "$work_dir/ca.key" -out "$work_dir/ca.crt" \
        -subj '/CN=ACS Laboratory CA' \
        -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign'
    mv "$work_dir/ca.key" "$tls_dir/ca.key"
    mv "$work_dir/ca.crt" "$tls_dir/ca.crt"
fi

openssl req -new -newkey rsa:2048 -noenc -sha256 \
    -keyout "$work_dir/server.key" -out "$work_dir/server.csr" \
    -subj "/CN=$acs_ip" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:$acs_ip" \
    -addext 'basicConstraints=critical,CA:FALSE' \
    -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
    -addext 'extendedKeyUsage=serverAuth'
openssl x509 -req -in "$work_dir/server.csr" \
    -CA "$tls_dir/ca.crt" -CAkey "$tls_dir/ca.key" \
    -set_serial "0x$(openssl rand -hex 16)" -days 365 -sha256 \
    -copy_extensions copy -out "$work_dir/server.crt"
openssl verify -purpose sslserver -verify_ip "$acs_ip" \
    -CAfile "$tls_dir/ca.crt" "$work_dir/server.crt"

if [[ -e $tls_dir/server.key || -e $tls_dir/server.crt ]]; then
    backup_dir=$(mktemp -d "$tls_dir/backup.XXXXXXXX")
    for name in server.key server.crt; do
        if [[ -f $tls_dir/$name ]]; then
            cp "$tls_dir/$name" "$backup_dir/$name"
        fi
    done
    echo "Certificado anterior preservado em: $backup_dir"
fi
mv "$work_dir/server.key" "$tls_dir/server.key"
mv "$work_dir/server.crt" "$tls_dir/server.crt"
chmod 600 "$tls_dir/ca.key" "$tls_dir/server.key"
if [[ ! -L $link ]]; then
    ln -s "$tls_dir" "$link"
fi
# Only the public CA certificate is exported to the Windows-mounted workspace.
cp "$tls_dir/ca.crt" "$backend_dir/.tls/lan-ca.crt"
echo "Certificados: $link"
echo "CA publica para importar na CPE/cliente: $backend_dir/.tls/lan-ca.crt"
echo "Reinicie o receptor com .tls/mirrored/server.crt e .tls/mirrored/server.key."

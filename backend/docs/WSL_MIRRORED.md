# ACS em WSL com rede espelhada

## Rede e endereço da CPE

O laboratório passou de NAT para `mirrored`. No ambiente verificado, o WSL
apresenta diretamente `192.168.18.7/24` na interface `eth1`. A CPE usa:

```text
https://192.168.18.7:7547/cwmp
```

O modo espelhado permite acesso direto pela LAN. O receptor precisa escutar em
`0.0.0.0:7547`, e o firewall precisa permitir essa conexão. Não recrie o
encaminhamento para o antigo IP interno `172.23.109.98`.

Verifique o modo e o endereço antes de configurar:

```bash
wslinfo --networking-mode
ip -brief -4 address
```

Reserve o endereço do computador no DHCP para evitar mudanças na ACS URL.
O endereço `192.168.18.1` é o gateway da rede, não o servidor ACS.

### Acesso pelo próprio Windows usando o IP da LAN

Para acessar o receptor pelo endereço `192.168.18.7` a partir do próprio Windows,
habilite `hostAddressLoopback` em `%USERPROFILE%\.wslconfig`, preservando as
demais opções existentes:

```ini
[wsl2]
networkingMode=mirrored

[experimental]
hostAddressLoopback=true
```

Essa opção trata a conexão Windows/WSL pelo IP do host. O acesso de outro
dispositivo da LAN é um caminho diferente e continua dependendo do firewall.
Sem a opção, o Windows pode alcançar `https://127.0.0.1:7547/docs`, mas dar
timeout ao usar `https://192.168.18.7:7547/docs`.

Após modificar o arquivo, salve seu trabalho e execute no PowerShell:

```powershell
wsl --shutdown
```

Esse comando encerra todas as distribuições WSL e seus processos. Reabra o WSL,
confirme Docker/PostgreSQL e inicie novamente o Uvicorn. A mudança só poderá ser
validada após essa reinicialização. Não é preciso renovar o certificado se o IP
continuar igual.

## Certificados da LAN

O modo de rede não altera as exigências de TLS: o certificado precisa ser
válido para o endereço usado na URL, estar dentro da validade e ser confiável
para a CPE. O certificado anterior de localhost não inclui o IP da LAN.

Em `backend/`, execute somente para a primeira emissão ou renovação:

```bash
bash scripts/create_lab_tls.sh 192.168.18.7
```

O script usa OpenSSL 3, cria uma CA local quando ainda não existe e emite um
certificado de servidor para `192.168.18.7`, `127.0.0.1` e `localhost`. A CA vale
10 anos e o certificado de servidor, 1 ano. Em novas execuções, a CA é reutilizada
e o certificado/chave de servidor anteriores são preservados em um diretório
`backup.*` antes da substituição. Reinicie os serviços para carregar a nova emissão.

Os arquivos ficam em `$HOME/.local/share/acs/lab-tls` por padrão, ou sob
`$XDG_DATA_HOME/acs/lab-tls` quando essa variável está definida. É possível definir
outro diretório Linux com `ACS_LAB_TLS_DIR`. O script exige permissões `700` no
diretório e aplica `600` às chaves. Isso evita depender do `chmod` em `/mnt/c` ou
`/mnt/d`, que pode não restringir o acesso conforme a montagem do Windows.

O diretório `backend/.tls/mirrored` é um link para esses arquivos. O certificado
público da CA também é exportado como `backend/.tls/lan-ca.crt`, acessível pelo
Windows. A pasta `.tls/` permanece ignorada pelo Git. Os arquivos anteriores
`.tls/server.crt` e `.tls/server.key` não são substituídos por esse script.

Importe **somente `lan-ca.crt`** na lista de autoridades confiáveis da CPE, se o
firmware oferecer essa opção. Não envie `ca.key` nem `server.key` para a CPE.
O campo de senha da chave privada exibido em alguns firmwares não substitui a
importação de uma CA confiável. Confirme também data/hora do equipamento.

Para confiar nessa CA no repositório do usuário Windows, execute conscientemente
no PowerShell, ajustando o caminho caso o projeto esteja em outro local:

```powershell
Import-Certificate -FilePath D:\projetos\acs_controll\backend\.tls\lan-ca.crt -CertStoreLocation Cert:\CurrentUser\Root
```

Clientes com repositório de confiança próprio podem exigir importação separada.
Se a CPE não permitir importar a CA, será necessário um certificado emitido por
uma autoridade que ela já reconheça e um nome DNS correspondente, ou outro
mecanismo suportado pelo firmware. A emissão local não instala confiança
automaticamente na CPE. Não desabilite a validação do certificado.

Se o IP mudar, execute o script com o novo IP, reinicie o receptor e atualize a
ACS URL. A CA permanece a mesma. Se apenas Windows/WSL reiniciarem e o endereço
continuar igual, não é necessário emitir outro certificado.

## Firewall e remoção do portproxy antigo

No **PowerShell como administrador**, a partir da raiz do projeto:

```powershell
netsh interface portproxy show all
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\backend\scripts\configure_wsl_mirrored.ps1 -ServerAddress 192.168.18.7 -AllowedRemoteAddresses 192.168.18.0/24
```

O script cria ou atualiza a regra `ACS-CWMP-WSL-Mirrored`, permitindo TCP 7547
para o WSL a partir da rede informada. Prefira informar o IP específico da CPE
quando não precisar de outros clientes de bancada. Remove apenas a regra
`portproxy` de `192.168.18.7:7547`, preservando os demais encaminhamentos. O
`ExecutionPolicy Bypass` vale somente para essa execução, sem alterar a política
permanente do PowerShell.

A sessão usada para preparar o projeto não tinha privilégios de administrador do
Windows; essa configuração precisa ser aplicada no terminal elevado. Foram
encontradas regras preexistentes permitindo a porta 7547 com origem `Any`.
Elas não são removidas pelo script: adicionar uma regra restrita não limita
outras regras permissivas existentes. Revise-as conforme a política da rede.

Durante a validação, iniciar o receptor em `0.0.0.0:7547` retornou
`address already in use`, embora `ss` no Linux não mostrasse um listener nessa
porta. Verifique também os listeners do Windows, pois a regra antiga pode manter
a porta ocupada no modo espelhado:

```powershell
Get-NetTCPConnection -LocalPort 7547 -State Listen
```

Depois de remover o portproxy do ACS, tente iniciar o receptor novamente. Não
adicione novos encaminhamentos para contornar a porta ocupada.

```powershell
Get-NetFirewallHyperVRule | Where-Object { $_.LocalPorts -contains '7547' } | Format-List Name,VMCreatorId,Enabled,Action,RemoteAddresses
netsh interface portproxy show all
```

Políticas corporativas e regras adicionais do Windows podem também afetar o
tráfego. Não é necessário liberar globalmente todas as conexões de entrada do WSL
nem desligar o firewall. A porta administrativa 8000 não é liberada pelo script.

## Inicialização

O banco permanece publicado apenas em loopback. Em `backend/`:

```bash
uv run acs-db-check
uv run uvicorn acs.cwmp.http.app:app --host 0.0.0.0 --port 7547 --no-access-log --no-proxy-headers --ssl-certfile .tls/mirrored/server.crt --ssl-keyfile .tls/mirrored/server.key
```

Em outro terminal, se precisar da API administrativa:

```bash
uv run uvicorn acs.api.app:app --host 127.0.0.1 --port 8000 --reload --no-access-log --no-proxy-headers --ssl-certfile .tls/mirrored/server.crt --ssl-keyfile .tls/mirrored/server.key
```

Use na CPE o usuário e a senha ACS cadastrados, com ACS Management e Periodic
Informing habilitados. O modo compartilhado ainda depende de
`ACS_CWMP_ALLOW_SHARED_CREDENTIALS=true`. Connection Request não é usado neste
fluxo. A rede espelhada não inicia os processos: após reiniciar, confirme Docker,
PostgreSQL e Uvicorn em execução.

## Diagnóstico sem expor credenciais

```bash
openssl verify -CAfile .tls/lan-ca.crt -purpose sslserver -verify_ip 192.168.18.7 .tls/mirrored/server.crt
curl --cacert .tls/lan-ca.crt --noproxy '*' --connect-timeout 5 -i -X POST https://192.168.18.7:7547/cwmp
```

Sem credenciais, o POST deve retornar `401`: confirma TLS e chegada à aplicação,
mas não valida o Inform autenticado. Erro de certificado exige verificar SAN,
validade e confiança; timeout exige verificar processo, firewall e regras antigas.
`Test-NetConnection 192.168.18.7 -Port 7547` valida apenas TCP.

Repita o teste a partir de outro dispositivo da LAN usando a CA pública.
Uma conexão feita dentro do próprio WSL não comprova acesso externo. A validação
final é receber `inform_accepted` e `session_completed`, com atualização do
inventário e das sessões da CPE.

No `curl.exe` do Windows, a CA local sem serviço de revogação pode produzir
`schannel: the revocation status is unknown`. Para diagnóstico desse certificado
de laboratório, é possível usar `--ssl-revoke-best-effort` junto de `--cacert`;
isso tolera indisponibilidade da informação de revogação, mantendo a verificação
da cadeia e do endereço. Não use `-k` nem altere globalmente a validação TLS.

```powershell
curl.exe --cacert D:\projetos\acs_controll\backend\.tls\lan-ca.crt --ssl-revoke-best-effort --noproxy "*" https://127.0.0.1:7547/docs
```

Os testes automatizados do emissor de certificados usam diretórios descartáveis
e verificam SAN, cadeia de confiança, permissões das chaves, rejeição de IP
inválido e renovação preservando a CA e o certificado anterior:

```bash
bash -n scripts/create_lab_tls.sh
uv run pytest tests/unit/test_lab_tls_script.py -q
```

A sintaxe do script PowerShell foi validada; a alteração efetiva do firewall
depende da execução elevada no Windows e não é comprovada pelos testes Linux.

Referências: [rede WSL espelhada](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking)
e [firewall Hyper-V](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/hyper-v-firewall).
Consulte também [hostAddressLoopback e reinicialização](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)
e [diagnóstico de revogação no curl](https://curl.se/docs/manpage.html#--ssl-revoke-best-effort).

# Backend ACS

## Normalização do inventário

O inventário básico recebido no Inform utiliza um perfil genérico para `Device`
e `InternetGatewayDevice`, com seleção do [perfil Huawei EG8145X6-10 / V5R022C00S364](docs/HUAWEI_PROFILE.md)
quando o Inform corresponde à combinação validada. Consulte
[normalização do inventário](docs/INVENTORY_NORMALIZATION.md) para campos aceitos,
atualizações parciais, validação e limites desta primeira entrega.

## Atualização de autenticação

Para desenvolver o frontend sem login, use
`ACS_API_ALLOW_ANONYMOUS_INVENTORY=true` no `.env` e reinicie a API. Em
desenvolvimento/teste, isso libera somente as consultas de CPEs, sessões e
eventos e remove a exigência de autenticação dessas operações no Swagger.
Consulte [consultas sem login](docs/AUTHENTICATION.md#consultas-sem-login-durante-o-desenvolvimento-do-frontend).
Para voltar a exigir login, defina `false` e reinicie.

Para criar e excluir credenciais CPE pelo Swagger, habilite
`ACS_ENABLE_TEST_CPE_ACCOUNTS=true` no `.env`, reinicie a API e autentique-se
com uma conta `administrator`. As rotas temporárias são
`POST /api/v1/test-cpe-accounts` e `DELETE /api/v1/test-cpe-accounts/{account_id}`.
Veja [API temporária de contas CPE](docs/AUTHENTICATION.md#api-temporária-de-contas-cpe).
Para fechar essas rotas, desabilite a opção e reinicie a API.

Para usar o mesmo usuário e senha ACS em várias CPEs provisionadas pelo IXC,
consulte [credencial compartilhada para IXC e bancada](docs/AUTHENTICATION.md#credencial-compartilhada-para-ixc-e-bancada).
Esse modo exige uma conta criada com `--shared` e a configuração
`ACS_CWMP_ALLOW_SHARED_CREDENTIALS=true`; HTTPS continua obrigatório.
O usuário compartilhado de referência da bancada é `acs_bancada`. Consulte o
guia acima para cadastro em um banco novo, configuração da CPE e rotação da
senha. A senha não é armazenada na documentação.

Por padrão, as requisições CWMP e de inventário exigem **HTTP Basic sobre HTTPS**
e contas previamente cadastradas. A opção acima libera apenas a leitura do
inventário no ambiente de desenvolvimento. Siga o [guia de autenticação](docs/AUTHENTICATION.md):
aplique a migration `0002_auth_accounts`, crie contas separadas para administradores
e CPEs com `uv run acs-account`, configure certificados TLS confiáveis e use
**Authorize** no Swagger. O histórico de sessões anônimas existentes é preservado.
O simulador contra o servidor real também exige HTTPS, identidade cadastrada e
credenciais. As aplicações continuam restritas a desenvolvimento/teste; a preparação
de segurança para produção ainda não está concluída.

## Configuração local

Pré-requisitos:

- `uv`;
- Docker com suporte ao Compose.

Crie a configuração local e sincronize o ambiente Python:

```bash
cp .env.example .env
uv sync
```

Inicie o PostgreSQL a partir da raiz do repositório e verifique a conexão:

```bash
docker compose up -d postgres
cd backend
uv run acs-db-check
```

O banco de desenvolvimento fica disponível apenas em `127.0.0.1:5432`.
O arquivo `.env` é local e não deve ser incluído em commits.

## API administrativa

Inicie a API a partir de `backend/`, com os certificados configurados.
Se `.tls/server.crt` e `.tls/server.key` ainda não existirem, o Uvicorn falhará
com `FileNotFoundError`, mesmo que o processo de reload anuncie a URL.

Para desenvolvimento local, gere um certificado autoassinado válido por 30 dias
para `localhost` e `127.0.0.1` (execute somente se os arquivos não existirem;
não substitua certificados já configurados):

```bash
mkdir -m 700 .tls
openssl req -x509 -newkey rsa:2048 -noenc -days 30 -keyout .tls/server.key -out .tls/server.crt -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' -addext 'basicConstraints=critical,CA:FALSE' -addext 'keyUsage=critical,digitalSignature,keyEncipherment' -addext 'extendedKeyUsage=serverAuth'
chmod 600 .tls/server.key
```

O diretório `.tls/` é ignorado pelo Git. Esse certificado local precisa ser
explicitamente confiado pelo cliente; não é um certificado para o IP da LAN ou
para homologar a CPE. Para esses casos, siga o [guia de TLS](docs/AUTHENTICATION.md#https-e-validação-em-bancada).

Inicie o servidor:

```bash
uv run uvicorn acs.api.app:app --reload --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
```

Valide o certificado local sem desabilitar a verificação TLS:

```bash
curl --cacert .tls/server.crt https://127.0.0.1:8000/health
```

O Swagger está em `https://127.0.0.1:8000/docs`. O navegador precisa confiar no
certificado local; ele não é adicionado automaticamente ao Windows/navegador.
Nos exemplos abaixo, `.tls/ca.crt` representa uma CA provisionada separadamente.
Se estiver usando o certificado autoassinado acima, use `.tls/server.crt` em
`--cacert`.

`GET https://127.0.0.1:8000/health` retorna `200` e `{"status":"healthy"}` quando
o processo da API está atendendo requisições. Esse endpoint público de disponibilidade
não verifica o PostgreSQL; use `uv run acs-db-check` para testar a conexão com o banco.

### Inventário de CPEs registradas

`GET /api/v1/cpes` lista as CPEs persistidas pelo Inform, usando a mesma configuração
PostgreSQL do receptor CWMP. Aplique as migrations antes de consultar. O endpoint
está no Swagger **administrativo**, em https://127.0.0.1:8000/docs, não na porta 7547.

```bash
curl --cacert .tls/ca.crt --user lab-reader 'https://127.0.0.1:8000/api/v1/cpes?limit=50&offset=0'
curl --cacert .tls/ca.crt --user lab-reader 'https://127.0.0.1:8000/api/v1/cpes?manufacturer=Huawei&serial_number=LAB000001'
```

A resposta contém `items`, `total` (após os filtros), `limit` e `offset`.
Cada item apresenta `id`, `manufacturer`, `oui`, `product_class`, `serial_number`,
`model`, `firmware`, `hardware`, `first_seen` e `last_seen`. Modelo e versões não
informados ficam como `null`. As datas incluem fuso horário. Credenciais, parâmetros
brutos e cookies de sessão não são retornados. Um inventário vazio retorna `200`
com `items: []`.

- `limit`: padrão 50, de 1 a 100; `offset`: padrão 0, não negativo.
- Filtros opcionais `manufacturer` e `serial_number`: correspondência exata,
  diferenciando maiúsculas e minúsculas, combinados com AND, de 1 a 64 caracteres cada.
- Ordenação: `last_seen` decrescente e, em seguida, `id` crescente. A última
  comunicação corresponde ao último Inform aceito, não a uma classificação online/offline.
- Contagem e itens usam a mesma visão consistente do banco por requisição. A posição
  dos registros entre páginas pode mudar quando novos Inform atualizam o inventário.
- Parâmetros de consulta inválidos retornam `422`; inventário indisponível retorna
  `503`, com `detail.code=inventory_unavailable` e correlação entre
  `detail.request_id` e `X-Request-ID`, sem detalhes internos do erro de banco.

A autenticação administrativa exige uma conta ativa com `inventory:read`.
O inventário só é habilitado em desenvolvimento/teste com SQL echo desabilitado;
caso contrário, a rota retorna `503`, enquanto `/health` continua disponível.
Mantenha a API restrita ao loopback ou a uma rede de laboratório isolada e autorizada.
Evite logs de acesso ao consultar filtros que identifiquem equipamentos:

```bash
uv run uvicorn acs.api.app:app --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
```

A entrega de consulta do inventário não adicionou colunas nem exigiu migration.
Filtros e opções de ordenação adicionais, assim como a gestão completa de
identidades e grupos, permanecem para etapas futuras.

### Inventário normalizado e rastreabilidade

`GET /api/v1/cpes/{cpe_id}/inventory` expõe modelo, firmware e hardware com
origem, perfil, horários e última observação. Também distingue capacidades
desconhecidas de recursos confirmados, sem presumir suporte a Wi-Fi, WAN, óptica
ou telefonia. Está disponível no Swagger e segue a política de leitura do
inventário, incluindo a exceção anônima de laboratório.

Consulte o [contrato completo](docs/INVENTORY_API.md) e a
[implementação de GetParameterNames/GetParameterValues](docs/READ_QUERIES.md).
O GET lê o banco. Para solicitar uma leitura remota pelo perfil Huawei homologado,
use `POST /api/v1/cpes/{cpe_id}/queries` e acompanhe
`GET /api/v1/cpes/{cpe_id}/queries/{operation_id}`. Essas duas rotas exigem
autenticação, mesmo quando o inventário anônimo está habilitado. O POST exige
papel `administrator`; a leitura aguarda a próxima sessão da CPE.
Aplique `uv run alembic upgrade head` e reinicie API e receptor CWMP.

### Detalhes, sessões e eventos da CPE

Utilize o `id` retornado pelo inventário, não o número de série. As três rotas estão
disponíveis no Swagger administrativo em https://127.0.0.1:8000/docs e usam o mesmo
banco e a mesma restrição a desenvolvimento/teste do inventário.

```bash
# Substitua por um id retornado por GET /api/v1/cpes.
CPE_ID='00000000-0000-0000-0000-000000000000'
curl --cacert .tls/ca.crt --user lab-reader "https://127.0.0.1:8000/api/v1/cpes/$CPE_ID"
curl --cacert .tls/ca.crt --user lab-reader "https://127.0.0.1:8000/api/v1/cpes/$CPE_ID/sessions?limit=50&offset=0"
curl --cacert .tls/ca.crt --user lab-reader "https://127.0.0.1:8000/api/v1/cpes/$CPE_ID/events?limit=50&offset=0"
```

- O detalhe retorna os mesmos campos públicos de um item da listagem, sem a estrutura
  de paginação. Modelo e versões ausentes permanecem `null`; nenhum novo RPC é enviado.
- Sessões retornam `items`, `total`, `limit` e `offset`. Cada item contém `id`,
  `cpe_id`, `version`, `state`, `started_at`, `expires_at` e `completed_at`.
  A ordenação é por `started_at` decrescente e depois pelo `id` da sessão crescente.
- Eventos usam a mesma estrutura de paginação. Cada item contém `session_id`,
  `position` (posição no Inform, começando em zero), `code` e `received_at`.
  A ordenação é por `started_at` da sessão decrescente, `id` da sessão crescente e
  `position` crescente. `received_at` é o horário de início da sessão no ACS, não
  o instante em que o evento ocorreu na CPE; o banco não armazena uma data independente
  para cada evento.
- Os históricos aceitam `limit` de 1 a 100 (padrão 50) e `offset` >= 0 (padrão 0).
  Contagem, existência da CPE e itens usam a mesma visão consistente por requisição.
- Uma CPE conhecida sem histórico, ou uma página além do último registro, retorna
  `200` e `items` vazio. O total continua contando todos os registros correspondentes.
- Um UUID desconhecido retorna `404` com `detail.code=cpe_not_found`.
  Indisponibilidade do banco/serviço retorna `503` com
  `detail.code=inventory_unavailable`. Esses erros incluem `detail.request_id`
  correlacionado com `X-Request-ID`. UUID malformado e paginação inválida retornam
  o erro padrão de validação `422` do FastAPI.

As consultas não alteram sessões: `awaiting_empty` pode já estar expirada, e
`completed` não significa que a CPE continua online. Tokens, hashes de tokens,
IDs de RPC, command keys, SOAP bruto e valores de parâmetros não são expostos.
Essas rotas apenas leem tabelas existentes e, por isso, não exigiram migration.

Os novos contratos HTTP de resposta usam modelos Pydantic; os registros internos
da aplicação continuam como dataclasses. Esta é a parte de backend dos requisitos
RF-028/RF-035 e parte do RF-036, além da consulta de sessões/eventos prevista no
roadmap. Não conclui a interface visual, a gestão completa de identidades, a
classificação online/offline, o inventário de IPs nem a atualização remota.

Valide os contratos HTTP e o comportamento no PostgreSQL a partir de `backend/`:

```bash
uv run pytest tests/contract/test_cpes.py -v
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/test_cwmp_persistence.py -v
```

Os testes PostgreSQL aplicam migrations e removem apenas schemas descartáveis
`test_cwmp_*`, preservando o inventário real do laboratório. Eles não exigem conexão
com uma CPE física.

## Receptor CWMP de desenvolvimento

Em `backend/`, com PostgreSQL em execução, `.env` e certificados configurados:

```bash
uv sync --locked
uv run alembic upgrade head
uv run uvicorn acs.cwmp.http.app:app --host 127.0.0.1 --port 7547 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
```

A aplicação CWMP separada atende `POST /cwmp`. Atualmente, aceita somente o perfil
Inform de CWMP 1.0. A inicialização é recusada fora de desenvolvimento/teste ou com
SQL echo habilitado. Consulte as [decisões CWMP](docs/CWMP_FIRST_DELIVERY.md) para o
escopo do protocolo e os cookies de sessão, e o [guia de autenticação](docs/AUTHENTICATION.md)
para as regras atuais de segurança.

### Swagger UI

- API administrativa: https://127.0.0.1:8000/docs apresenta `GET /health`, listagem
  de CPEs, detalhes individuais, sessões e eventos.
- Receptor CWMP: https://127.0.0.1:7547/docs apresenta `POST /cwmp`.

Execute os comandos das duas aplicações em terminais separados.
No Swagger CWMP, use **Authorize** com uma conta cuja identidade corresponda ao
exemplo, abra `POST /cwmp`, selecione **Try it out** e execute o exemplo fictício
`inform` como `text/xml`. O retorno esperado é `200` com InformResponse em XML.
Depois, apague completamente o corpo da requisição, sem espaços, aspas ou quebras
de linha, e execute novamente em até 60 segundos: o retorno esperado é `204`, sem corpo.
O navegador gerencia automaticamente o cookie HttpOnly na mesma origem.
As requisições gravam no banco configurado; use somente dados de desenvolvimento/teste.
O exemplo mínimo exercita o receptor, não a conformidade completa de uma CPE.
Os recursos visuais do Swagger exigem acesso à internet para carregar a CDN padrão.

### Acesso LAN no WSL espelhado (atual)

O laboratório agora usa `networkingMode=mirrored`, com IP `192.168.18.7` no WSL.
Siga o [guia de rede espelhada e certificados](docs/WSL_MIRRORED.md) para emitir
o certificado da LAN, importar a CA e configurar o firewall. O redirecionamento
antigo para `172.23.109.98` deve ser removido, não recriado nesse modo.

Os scripts `scripts/create_lab_tls.sh` e `scripts/configure_wsl_mirrored.ps1`
preparam respectivamente os certificados locais e a configuração do Windows.
O segundo exige PowerShell como administrador. A URL da CPE continua sendo
`https://192.168.18.7:7547/cwmp` enquanto esse for o IP do servidor.

Inicie o receptor em `backend/` com o certificado da LAN:

```bash
uv run uvicorn acs.cwmp.http.app:app --host 0.0.0.0 --port 7547 --no-access-log --no-proxy-headers --ssl-certfile .tls/mirrored/server.crt --ssl-keyfile .tls/mirrored/server.key
```

### Referência: acesso LAN no WSL2 com NAT

Os comandos abaixo são mantidos para uma eventual volta ao modo NAT. Não execute
a criação do portproxy enquanto estiver usando a rede espelhada.

No laboratório com rede NAT, mantenha o PostgreSQL ativo e inicie o receptor em
`backend/`, no WSL, escutando também fora do loopback:

```bash
uv run uvicorn acs.cwmp.http.app:app --host 0.0.0.0 --port 7547 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
```

Confirme os endereços atuais com `ip -4 addr show eth0` no WSL e `ipconfig` no Windows.
Os endereços usados no laboratório eram Windows `192.168.18.7` e WSL `172.23.109.98`;
substitua-os se mudarem. O endereço do Windows deve ser o IPv4 do computador na LAN,
não o gateway padrão.

Se o acesso LAN parar de funcionar, abra o **PowerShell como administrador**, confira
as regras, remova as duas regras sobrepostas do ACS e recrie apenas a regra da LAN:

```powershell
netsh interface portproxy show all

netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=7547
netsh interface portproxy delete v4tov4 listenaddress=192.168.18.7 listenport=7547

netsh interface portproxy add v4tov4 listenaddress=192.168.18.7 listenport=7547 connectaddress=172.23.109.98 connectport=7547
```

Remova somente entradas deste laboratório. Se uma regra antiga utilizar outro IP
do Windows, informe esse endereço antigo ao excluí-la. Uma regra inexistente pode
retornar uma mensagem de que não foi encontrada; não apague regras de outros serviços.

Verifique tanto a conexão direta com o WSL quanto a conexão encaminhada:

```powershell
Get-NetTCPConnection -LocalPort 7547 -State Listen
Test-NetConnection 172.23.109.98 -Port 7547
Test-NetConnection 192.168.18.7 -Port 7547
```

Se o WSL responder, mas ainda não houver um serviço escutando no IP LAN do Windows,
reinicie o IP Helper e repita as verificações. Isso pode interromper temporariamente
outros encaminhamentos do Windows:

```powershell
Restart-Service iphlpsvc
```

Restrinja a regra do firewall aos IPs da CPE e dos dispositivos de teste autorizados.
Teste `https://192.168.18.7:7547/openapi.json` a partir de um dispositivo autorizado
na LAN, com a confiança no certificado configurada. Na CPE, configure a URL ACS
`https://192.168.18.7:7547/cwmp`. Um teste no próprio Windows não comprova que o
firewall permite conexões recebidas da LAN.

O encaminhamento não inicia Uvicorn nem PostgreSQL. Após reiniciar, confirme que
ambos estão em execução e atualize o destino do encaminhamento se o IP do WSL mudar.
Não exponha este receptor de desenvolvimento à internet.

## Validação

A persistência de [origem, perfil e horários por campo](docs/INVENTORY_PROVENANCE.md)
requer `uv run alembic upgrade head` antes de iniciar o receptor atualizado.
A migration preserva os valores existentes; metadados são obtidos dos próximos Informs.

### Coleta de evidências da CPE de bancada

Para preparar um perfil de normalização a partir de um Inform real, utilize a
[coleta controlada e anonimizada](docs/INFORM_CAPTURE.md). Ela seleciona uma CPE
por UUID, tem prazo e salva no máximo uma fixture parcial. É desativada por padrão
e não grava XML bruto nem credenciais.

### Simulador de CPE como testes pytest

O simulador existe apenas em `tests/cwmp_simulator.py`, utiliza identidade fictícia
e horário UTC atual e executa Inform/InformResponse seguido de POST vazio.
Verifica namespaces SOAP, ID devolvido, MaxEnvelopes=1, cookies e HTTP 200/204.
Não implementa outros RPCs nem acessa o PostgreSQL diretamente.

Execute os testes do simulador sem Docker ou servidor ativo:

```bash
uv run pytest tests/unit/test_cpe_simulator.py -v
```

Para testar pela rede, inicie o PostgreSQL, aplique as migrations e execute o receptor
CWMP com os comandos anteriores. Em outro terminal, dentro de `backend/`:

```bash
ACS_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_live_cpe_simulator.py -v
```

O teste contra servidor real é desabilitado por padrão e acessa somente
`https://127.0.0.1:7547/cwmp`, sem redirecionamentos ou proxies do ambiente.
Configure a identidade fictícia cadastrada, as credenciais e a CA conforme o
[guia de autenticação](docs/AUTHENTICATION.md). Ele executa duas sessões com essa
identidade, criando ou atualizando a CPE fictícia e adicionando duas sessões ao
banco de desenvolvimento configurado; não remove registros. Use um banco de
laboratório, não de produção. Falhas ou indisponibilidade do receptor fazem o teste
falhar, em vez de ignorá-lo silenciosamente. Não use `--showlocals`, logs HTTP de
depuração ou credenciais reais nesses testes.

Verifique persistência e idempotência separadamente, em um schema PostgreSQL
descartável com migrations aplicadas, sem precisar iniciar Uvicorn:

```bash
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration/test_cwmp_persistence.py -k simulated -v
```

Esse teste confirma uma CPE, duas sessões concluídas e dois eventos. O teste pela
rede, sozinho, verifica a troca de mensagens, não as linhas do banco. O simulador
mínimo não é um teste completo de conformidade TR-069 e não substitui a validação
com uma CPE Huawei real.

### Verificações de qualidade

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

Execute os testes de integração com PostgreSQL enquanto o serviço do Compose estiver saudável:

```bash
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -m integration
```

## Coleta WAN e óptica

A entrega de endereços IPv4/IPv6, MAC, DNS e potência óptica usa descoberta
controlada e consultas por perfil. Consulte [contrato, comandos, evidências e
limitações](docs/WAN_OPTICAL_TELEMETRY.md). O endpoint
`GET /api/v1/cpes/{cpe_id}/telemetry` está disponível no Swagger da API.

## Evolução da compatibilidade por evidências

O [desenho de referência](docs/CPE_COMPATIBILITY_ARCHITECTURE.md) define coleta persistente,
pacotes de evidência, catálogo observado, perfis declarativos versionados e
reprodução offline. O documento distingue a base existente das próximas entregas
e estabelece critérios de homologação por campo/modelo/firmware.

## Ponto de retomada

Consulte [estado atual, validações e próximo incremento](docs/WORK_STATUS.md)
antes de retomar a evolução da coleta e dos perfis de compatibilidade.

## Pacote de evidências offline

A CLI `acs-evidence create/verify` transforma uma captura em pacote parcial
com XML anonimizado e manifesto verificável. Consulte [comandos e limites](docs/EVIDENCE_PACKAGE.md).

# Identificação autenticada e acesso de leitura em laboratório

## Escopo e decisões

Esta entrega utiliza contas locais no PostgreSQL e HTTP Basic **somente sobre HTTPS**.
Para desenvolver o frontend antes do login, há uma exceção explícita e temporária
de leitura anônima do inventário, descrita abaixo.
É a primeira etapa de autenticação, não uma plataforma de identidade pronta para
produção nem uma implementação completa dos requisitos RF-001 a RF-007.
Nenhum provedor OIDC foi definido. A interface de autenticação da aplicação permite
substituir a consulta local de credenciais futuramente. Digest não está implementado;
não envie credenciais Basic por HTTP nem desative a validação de certificados para
acomodar um equipamento.

Basic transmite uma senha reutilizável, portanto TLS é obrigatório:
[RFC 7617](https://www.rfc-editor.org/rfc/rfc7617.html).
As senhas são armazenadas como hashes scrypt versionados, com salts aleatórios
individuais de 16 bytes e os parâmetros N=2^14, r=8, p=5 e dkLen=32. Esse é um dos
perfis documentados no
[guia de armazenamento de senhas da OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).
Não foram adicionadas dependências. A verificação de senha ocorre fora do loop de
eventos, com no máximo quatro verificações simultâneas por autenticador e tempo
limitado de espera para iniciar uma verificação.

As restrições atuais de inicialização em desenvolvimento/teste, com SQL echo
desabilitado, permanecem. A implementação da autenticação não significa que este
ACS já possa ser exposto publicamente.

## Contas e autorização

- Contas administrativas e de equipamentos pertencem a categorias separadas
  (`admin` / `cpe`), com senhas independentes. Uma credencial de equipamento não
  permite consultar o inventário; uma credencial administrativa não permite se
  apresentar como equipamento.
- Por padrão, contas de CPE são previamente cadastradas e vinculadas a OUI,
  ProductClass e SerialNumber. A identidade é verificada no serviço de aplicação
  antes da persistência do Inform. O perfil compartilhado para provisionamento
  em massa é uma exceção explícita, descrita abaixo.
- Todo POST, incluindo a continuação com corpo vazio, exige nova autenticação.
  O hash armazenado do token de sessão incorpora o UUID da conta autenticada.
  Possuir o cookie de outra conta não basta para continuar sua sessão. Os cookies
  continuam com HttpOnly e Secure sobre HTTPS. Sessões anônimas existentes não
  podem ser continuadas; elas permanecem como registros históricos.
- Os papéis administrativos `administrator` e `support` concedem `inventory:read`.
  `administrator` também concede `cpe:query` para solicitar as
  [consultas remotas por perfil](READ_QUERIES.md). Criação e acompanhamento de
  operações exigem autenticação mesmo com inventário anônimo no laboratório.
  Somente `administrator` também concede `cpe_accounts:write`, utilizada pelas
  rotas temporárias de contas CPE quando habilitadas. O papel `pending` não
  concede acesso. Cada caso de uso do inventário verifica a permissão; um serviço sem
  identidade autenticada vinculada nega o acesso.
- Contas desativadas são rejeitadas nas próximas requisições. A troca de senha
  entra em vigor na próxima requisição e preserva o UUID da conta. Uma sessão
  ativa e válida só pode continuar com a nova senha.
- Credenciais e hashes não aparecem nas respostas do inventário. As senhas são
  solicitadas sem exibição no terminal e nunca são passadas como argumentos da
  linha de comando. Nenhuma conta padrão é criada automaticamente.

## Cadastro inicial

Em `backend/`, com o PostgreSQL em execução:

```bash
uv sync --locked
uv run alembic upgrade head
uv run acs-account create admin lab-reader --role support
uv run acs-account create admin lab-admin --role administrator
uv run acs-account create cpe lab-device --oui A1B2C3 --product-class LAB-ONT --serial-number LAB000001
```

Utilize a identidade exata informada pela CPE de bancada, não os dados fictícios
do exemplo. O registro existente em `cpes` fornece OUI, `product_class` e serial.
O comando cadastra credenciais sem alterar ou criar dados de inventário.
Use senhas exclusivas com pelo menos 14 caracteres e no máximo 1024 bytes em UTF-8.
Omitir `--role` ao criar uma conta administrativa atribui o papel `pending`, sem
permissão para ler o inventário. Nomes de usuário duplicados na mesma categoria
ou identidades de CPE duplicadas são rejeitados, sem sobrescrever credenciais.

```bash
uv run acs-account rotate cpe lab-device
uv run acs-account disable cpe lab-device
uv run acs-account rotate admin lab-reader
uv run acs-account disable admin lab-reader
```

A troca de senha não reativa uma conta desativada. O cadastro e a exclusão de
contas CPE também estão disponíveis nas rotas temporárias descritas abaixo.
Ativação, alteração de papel e cadastro de administradores continuam fora da API.
Esses comandos locais são privilegiados: restrinja o acesso ao sistema operacional
e ao banco. A saída contém apenas ação, UUID da conta e categoria; ela ainda não
constitui uma trilha persistente de auditoria administrativa.

## Consultas sem login durante o desenvolvimento do frontend

Para adiar o login do frontend, defina no `backend/.env` e reinicie a API:

```dotenv
ACS_API_ALLOW_ANONYMOUS_INVENTORY=true
```

Essa opção tem padrão `false` e só libera consultas em `development` ou `test`,
com SQL echo desabilitado. As seguintes rotas deixam de exigir credenciais:

- `GET /api/v1/cpes`
- `GET /api/v1/cpes/{cpe_id}`
- `GET /api/v1/cpes/{cpe_id}/sessions`
- `GET /api/v1/cpes/{cpe_id}/events`
- `GET /api/v1/cpes/{cpe_id}/inventory`

O Swagger remove a exigência de autenticação dessas operações. A aplicação aceita
as leituras anônimas por HTTP ou HTTPS; o comando Uvicorn configurado com TLS
continua atendendo somente HTTPS. Não é necessário mudar o servidor TLS para usar
essa opção. Exemplo com o certificado local:

```bash
curl --cacert .tls/server.crt https://127.0.0.1:8000/api/v1/cpes
```

Qualquer cliente que alcance essas rotas poderá ler o inventário enquanto o modo
estiver habilitado. Use apenas no ambiente de desenvolvimento com acesso de rede
restrito. A opção não configura CORS: se o frontend estiver em outra origem,
a origem autorizada deverá ser configurada separadamente.

O serviço de aplicação continua exigindo a permissão de leitura. O adaptador
HTTP fornece uma identidade interna somente de leitura, de UUID zero, para
identificar esse modo nos logs. Não é criada uma conta administrativa no banco.
As rotas de criação/exclusão de contas CPE continuam exigindo administrador e
HTTPS; o receptor `/cwmp` mantém a autenticação das CPEs.

Para retomar a autenticação do inventário, defina a opção como `false` e reinicie
a API. As mesmas rotas passam a exigir HTTP Basic sobre HTTPS e `inventory:read`
novamente. Produção e homologação não aceitam essa liberação temporária.

## API temporária de contas CPE

Em `backend/.env`, habilite `ACS_ENABLE_TEST_CPE_ACCOUNTS=true` e reinicie a API
administrativa. O padrão é `false`. As rotas só são registradas em
`development`/`test`, com SQL echo desabilitado. Quando desabilitadas, retornam
`404` e não aparecem no Swagger. Para fechar o acesso posteriormente, volte a
opção para `false` e reinicie a API; as contas cadastradas permanecem válidas.

Crie um administrador, se ainda não houver um:

```bash
uv run acs-account create admin lab-admin --role administrator
```

Abra `https://127.0.0.1:8000/docs` e use **Authorize** com essa conta administrativa.
Contas CPE e administradores com papel `support` não podem gerenciar credenciais.
HTTPS é obrigatório.

- `POST /api/v1/test-cpe-accounts`: cria uma credencial CPE, retornando `201`
  com `id`, `username` e `shared`. Senhas e hashes nunca são retornados.
- `DELETE /api/v1/test-cpe-accounts/{account_id}`: exclui uma credencial CPE
  pelo UUID retornado no cadastro e responde `204`. Conta inexistente ou UUID
  de administrador retorna `404`. Não há exclusão de contas administrativas.

Exemplo de corpo para um perfil compartilhado (substitua a senha ilustrativa):

```json
{
  "username": "cpe_teste_02",
  "password": "SUBSTITUA-POR-UMA-SENHA-UNICA",
  "shared": true
}
```

Para conta individual, use `shared: false` e informe `oui`, `product_class` e
`serial_number`. OUI aceita seis dígitos hexadecimais e é normalizado para
maiúsculas. Perfis compartilhados não aceitam campos de identidade. A senha
exige pelo menos 14 caracteres, no máximo 1024 bytes UTF-8 e nenhum caractere
de controle. Usuário ou identidade duplicados retornam `409`, sem sobrescrever
a conta. Dados inválidos retornam `422`, sem reproduzir o conteúdo enviado.

Excluir a conta revoga as próximas autenticações, inclusive o POST vazio de uma
sessão CWMP aberta. Requisições já autenticadas podem terminar. O inventário,
as sessões e os eventos históricos são preservados. Para contas compartilhadas,
a exclusão afeta todas as CPEs que usam essa credencial.

As rotas podem excluir qualquer conta da categoria `cpe` desse banco, inclusive
as criadas pela CLI; não há uma classificação separada de contas de teste.
Use somente no banco de laboratório. Os logs registram ação, administrador e
correlação da requisição sem registrar a senha. A auditoria persistente permanece
pendente. Nenhuma migration é necessária: as rotas usam a tabela `auth_accounts`
existente.

## Credencial compartilhada para IXC e bancada

Para configurações automáticas do IXC que repetem ACS Username e ACS Password,
cadastre um perfil compartilhado. O usuário de referência para testes é
`acs_bancada`, já criado no banco local da bancada. Ele não é criado
automaticamente pelas migrations e não concede acesso administrativo.

Em um banco novo, execute em `backend/`:

```bash
uv run alembic upgrade head
uv run acs-account create cpe acs_bancada --shared
```

O comando solicita uma senha de pelo menos 14 caracteres sem exibi-la. Nenhuma
senha padrão é embutida no código ou criada automaticamente. Não combine
`--shared` com OUI, ProductClass ou SerialNumber.
Se a conta já existir, não repita o cadastro; use o comando de rotação abaixo
para definir outra senha. A senha não é publicada nesta documentação nem
versionada no repositório; guarde-a em um gerenciador de senhas.

No arquivo local `backend/.env`, defina:

```dotenv
ACS_CWMP_ALLOW_SHARED_CREDENTIALS=true
```

Reinicie o receptor CWMP para carregar a configuração. O valor padrão é `false`:
sem habilitação explícita, contas compartilhadas recebem `401`, mesmo com senha
correta. Contas individuais continuam disponíveis nos dois modos. A migration
preserva as contas existentes como individuais.

Configure nas CPEs:

- **ACS URL:** `https://IP_OU_DNS_DO_ACS:7547/cwmp`, com certificado confiável.
- **ACS User Name:** `acs_bancada` (com sublinhado).
- **ACS Password:** a senha definida no cadastro local, repetida nos equipamentos autorizados.
- **Connection Request:** os campos não são usados nesta entrega.

Na bancada atual, a URL configurada é `https://192.168.18.7:7547/cwmp`.
Esse endereço depende da rede local; o certificado precisa incluir o IP e ser
confiável para a CPE. Habilite **ACS Management** e **Periodic Informing**;
o intervalo de 20 segundos é usado apenas nos testes de bancada.

Cada Inform continua registrando a CPE pela combinação OUI, ProductClass e
SerialNumber, com sessões e eventos próprios. Repetir o Inform não duplica a CPE.
O perfil compartilhado autentica o grupo, mas **não comprova a identidade física
do equipamento**: qualquer cliente com essa credencial pode declarar outra
identidade. Não há lista de identidades permitidas neste modo. Restrinja o acesso
à rede de gerenciamento e mantenha HTTPS.

O cookie é vinculado à conta, não ao equipamento físico. Um Inform com identidade
diferente não pode reutilizar uma sessão aberta; porém, um cliente que possua a
credencial compartilhada e o cookie de outro equipamento do mesmo perfil pode
enviar o POST vazio dessa sessão, pois essa requisição não contém DeviceId.

```bash
uv run acs-account rotate cpe acs_bancada
uv run acs-account disable cpe acs_bancada
```

A rotação exige atualizar a senha em todas as CPEs desse perfil; a desativação
bloqueia todas elas na próxima requisição. Voltar a configuração para `false` e
reiniciar o receptor também bloqueia os perfis compartilhados. A conta não concede
acesso à API administrativa. O downgrade da migration 0003 é bloqueado enquanto
houver contas compartilhadas, evitando sua remoção silenciosa.

## HTTPS e validação em bancada

Para o laboratório atual com WSL em modo espelhado e endereço `192.168.18.7`,
siga o [guia de rede espelhada](WSL_MIRRORED.md). Ele utiliza certificados em
`.tls/mirrored/`, com SAN para o IP da LAN, e exporta `.tls/lan-ca.crt` para
importação nos clientes. Os exemplos genéricos abaixo com `.tls/server.*`
pressupõem que esses arquivos tenham sido provisionados para o endereço usado.

Providencie um certificado cujo SAN corresponda ao IP ou nome DNS configurado na
CPE, emitido por uma autoridade certificadora (CA) em que o equipamento confie.
Coloque certificado e chave em `backend/.tls/`, diretório ignorado pelo Git, e
restrinja as permissões da chave privada. Um certificado autoassinado só pode ser
usado quando o cliente confia explicitamente nele; não desative a verificação.

```bash
uv run uvicorn acs.cwmp.http.app:app --host 0.0.0.0 --port 7547 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
uv run uvicorn acs.api.app:app --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
```

Em modo NAT, o portproxy do Windows encaminha TCP e transporta TLS sem descriptografar
o tráfego. Em modo espelhado, use o acesso direto e remova a regra antiga do ACS.
Restrinja o firewall aos equipamentos autorizados no laboratório.
Na CPE, use `https://IP_DO_WINDOWS_NA_LAN:7547/cwmp` e configure **ACS Username e
Password**, não os campos de Connection Request. Nunca reutilize as credenciais
do provedor anterior. Connection Request continua sem implementação.

Abra `https://127.0.0.1:8000/docs` (o certificado precisa incluir esse endereço) e
clique em **Authorize** usando uma conta administrativa. O Swagger CWMP na porta
7547 possui seu próprio **Authorize**, que recebe a conta da CPE. A identidade do
Inform de exemplo precisa corresponder à conta quando ela é individual. Por padrão, o Swagger não mantém
a autorização ao recarregar a página. `GET /health`, a documentação e o OpenAPI
continuam públicos; as rotas de inventário exigem autenticação por padrão,
exceto quando a leitura anônima de desenvolvimento é explicitamente habilitada.

Para consultar o inventário pela linha de comando, deixe o curl solicitar a senha,
sem incluí-la no histórico do terminal. Informe o arquivo da CA quando ela não
estiver no repositório de certificados confiáveis do sistema:

```bash
curl --cacert .tls/ca.crt --user lab-reader https://127.0.0.1:8000/api/v1/cpes
```

Não habilite confiança irrestrita em cabeçalhos encaminhados. Os comandos de TLS
direto acima desabilitam os cabeçalhos de proxy. Se a terminação TLS passar a ser
feita por um proxy, defina explicitamente quais proxies são confiáveis antes de
aceitar a indicação de HTTPS encaminhada por cabeçalho.

## Respostas e compatibilidade

- Rotas protegidas por HTTPS sem credenciais válidas: `401` e `WWW-Authenticate: Basic`.
- HTTP sem criptografia em rota protegida ou identidade da CPE incompatível:
  `403`, sem confirmação do Inform. Um servidor Uvicorn configurado somente para
  TLS pode rejeitar HTTP antes que a requisição alcance a aplicação.
- Conta administrativa autenticada sem `inventory:read`: `403`.
- Falha no banco de autenticação ou no limite de processamento: `503`, sem liberar
  acesso anônimo como alternativa.
- Cookie de sessão de outra conta: sessão inválida (`400`), sem mudança de estado.
- Sessão autenticada válida: `200 InformResponse` seguido de `204`, como antes.

Esses são erros HTTP de autenticação/autorização, não novos faults CWMP.
Os faults SOAP 8000/8003 permanecem inalterados. O receptor não habilita mais CORS
com origem curinga: o Swagger na mesma origem funciona sem permitir sites arbitrários.
O teste de bancada de 26/10/2026 validou HTTPS/Basic e os dois RPCs de leitura
na Huawei EG8145X6-10 / V5R022C00S364; veja [evidências e limites](READ_QUERIES.md).
Testes com simuladores não comprovam conformidade CWMP completa nem compatibilidade
da autenticação com a Huawei.

## Testes

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
```

O simulador de execução opcional contra o servidor real exige uma conta cadastrada
com OUI `A1B2C3`, ProductClass `SIMULATOR` e o serial definido em
`ACS_SIMULATOR_SERIAL`. Forneça `ACS_SIMULATOR_USERNAME` e `ACS_SIMULATOR_PASSWORD`
por um ambiente protegido e, opcionalmente, `ACS_SIMULATOR_CA_FILE` com o caminho
da CA confiável. Não exiba segredos nem use `--showlocals` no pytest. O simulador
acessa HTTPS em localhost, valida o certificado e envia as mesmas credenciais nas
duas requisições; nunca utiliza `verify=False`.

```bash
ACS_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_live_cpe_simulator.py -v
```

## Itens pendentes

Digest e interoperabilidade, se exigidos pelo firmware; OIDC ou sessões de login/logout
administrativo; auditoria persistente; API de gestão de contas; administração de grupos
e MFA; limites de requisições por conta/IP e distribuídos; recuperação de contas;
expiração de credenciais; implantação e homologação TLS; revisão de segurança.

Limitar o processamento de hashes não constitui uma defesa completa contra força
bruta. Basic não mantém uma sessão de login no servidor e não oferece logout no
servidor: desativar a conta revoga o acesso, enquanto remover a autorização no
navegador apenas limpa a credencial no cliente. As restrições de inicialização em
produção/homologação continuam ativas.

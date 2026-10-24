# Identificação autenticada e acesso de leitura em laboratório

## Escopo e decisões

Esta entrega utiliza contas locais no PostgreSQL e HTTP Basic **somente sobre HTTPS**.
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
- Os papéis administrativos `administrator` e `support` concedem, nesta etapa,
  somente `inventory:read`. O papel `pending` não concede acesso. Esses papéis
  ainda não autorizam alterações de configuração nem endpoints HTTP de gestão
  de contas. Cada caso de uso do inventário verifica a permissão; um serviço sem
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

A troca de senha não reativa uma conta desativada. Ainda não existem endpoints
públicos para cadastro, exclusão, ativação ou alteração de papel de uma conta.
Esses comandos locais são privilegiados: restrinja o acesso ao sistema operacional
e ao banco. A saída contém apenas ação, UUID da conta e categoria; ela ainda não
constitui uma trilha persistente de auditoria administrativa.

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
- **ACS Password:** a senha definida no comando, repetida nos equipamentos.
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

Providencie um certificado cujo SAN corresponda ao IP ou nome DNS configurado na
CPE, emitido por uma autoridade certificadora (CA) em que o equipamento confie.
Coloque certificado e chave em `backend/.tls/`, diretório ignorado pelo Git, e
restrinja as permissões da chave privada. Um certificado autoassinado só pode ser
usado quando o cliente confia explicitamente nele; não desative a verificação.

```bash
uv run uvicorn acs.cwmp.http.app:app --host 0.0.0.0 --port 7547 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
uv run uvicorn acs.api.app:app --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers --ssl-certfile .tls/server.crt --ssl-keyfile .tls/server.key
```

O portproxy existente no Windows encaminha TCP: ele transporta TLS sem descriptografar
o tráfego. Restrinja o firewall do Windows aos equipamentos autorizados no laboratório.
Na CPE, use `https://IP_DO_WINDOWS_NA_LAN:7547/cwmp` e configure **ACS Username e
Password**, não os campos de Connection Request. Nunca reutilize as credenciais
do provedor anterior. Connection Request continua sem implementação.

Abra `https://127.0.0.1:8000/docs` (o certificado precisa incluir esse endereço) e
clique em **Authorize** usando uma conta administrativa. O Swagger CWMP na porta
7547 possui seu próprio **Authorize**, que recebe a conta da CPE. A identidade do
Inform de exemplo precisa corresponder à conta quando ela é individual. Por padrão, o Swagger não mantém
a autorização ao recarregar a página. `GET /health`, a documentação e o OpenAPI
continuam públicos; as rotas de inventário exigem autenticação.

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

- HTTPS sem credenciais válidas: `401` e `WWW-Authenticate: Basic`.
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
O teste anterior com a Huawei validou somente HTTP anônimo. HTTPS/Basic e a confiança
no certificado ainda exigem um novo teste em bancada para o firmware utilizado.
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

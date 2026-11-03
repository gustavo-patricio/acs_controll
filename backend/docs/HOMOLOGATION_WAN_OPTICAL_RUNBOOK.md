# Roteiro de homologação WAN e óptica — EG8145X6-10

Este roteiro usa a API administrativa HTTPS e o receptor CWMP existentes. Requer
`curl`, `jq` e Python 3 no terminal. Execute em `backend`. Substitua URL e usuário pelos do ambiente; `curl --user "$ACS_ADMIN_USER"`
pede a senha interativamente. A conta precisa do papel `administrator` para criar
consultas (`cpe:query`). `support` pode ler inventário/estado, mas não agendar RPCs.
A API usa a porta administrativa (exemplo 8000), não a porta CWMP 7547.

```bash
export ACS_API_URL='https://localhost:8000'
export ACS_CA='.tls/server.crt'
export ACS_ADMIN_USER='SEU_ADMINISTRADOR'
```

## 1. Selecionar a CPE

```bash
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes?manufacturer=Huawei%20Technologies%20Co.%2C%20Ltd&limit=100" \
  | jq '.items[] | {id,product_class,model,firmware,last_seen}'
export CPE_ID='UUID_DA_CPE_ESCOLHIDA'
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/inventory" | jq .
```

Confirme fabricante, ProductClass `EG8145X6-10`, OUI `00259E`, hardware `343D.D`
e firmware `V5R022C00S364`. `last_seen` mostra o último Inform aceito, não prova
que a CPE esteja online agora. Há perfil de inventário X6 e um rascunho de
mapeamento, mas nenhuma aprovação óptica/WAN. O mecanismo de rascunho da CPE está
em `GET /api/v1/cpes/$CPE_ID/profiles/draft`; 404 significa que ainda não foi
iniciada uma validação, não que a CPE seja incompatível.

## 2. Descobrir nomes, um nível por operação

Gere uma chave para cada intenção nova. Esta chamada envia GPN com
`ParameterPath=InternetGatewayDevice.` e `NextLevel=true`:

```bash
export QUERY_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"
export BRANCH='InternetGatewayDevice.'
export OP_ID="$(curl --fail-with-body --silent --show-error --cacert "$ACS_CA" \
  --user "$ACS_ADMIN_USER" -H 'Content-Type: application/json' \
  --data "{\"kind\":\"get_parameter_names\",\"scope\":\"discovery\",\"parameter_path\":\"$BRANCH\",\"idempotency_key\":\"$QUERY_KEY\",\"capture_evidence\":true}" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries" | jq -r '.id')"
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/$OP_ID" | jq '{state,failure_reason,result,attempt_count}'
```

HTTP 202 e `pending` significam apenas que a operação foi enfileirada. Aguarde o
próximo Inform da CPE: o receptor envia InformResponse; no POST vazio seguinte
entrega o GPN. Atualize o GET até `succeeded`, `failed` ou `expired`. Uma consulta
pendente vence em até dez minutos; depois do despacho, em até sessenta segundos.
Não envie o RPC diretamente à ConnectionRequestURL. Não reinicie a ONU se ela for
seu acesso de trabalho.

Se `WANDevice.` aparecer, repita o POST acima com `BRANCH` igual ao caminho
completo retornado, por exemplo `InternetGatewayDevice.WANDevice.`. Depois navegue
por **cada** objeto concreto devolvido, inclusive `WANDevice.<n>.`,
`WANConnectionDevice.<n>.` e `WANPPPConnection.<n>.` que de fato existirem. O ramo
`WANConnectionDevice.2` visto em um Inform é evidência daquela unidade, não índice
universal. A mesma regra vale para objetos ópticos e de outras WANs. Para uma
varredura limitada persistida, existe `POST /api/v1/cpes/$CPE_ID/queries/collections`
com `kind=discovery`, `root`, `max_depth`, `max_rpcs`, `max_entries`; acompanhe
`GET .../collections/{job_id}`. Consulte [limites](BOUNDED_COLLECTIONS.md). O
roteiro manual permite revisar cada ramo antes de ler valores.

## 3. Revisar catálogo e candidatos

```bash
curl --get --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  --data-urlencode 'prefix=InternetGatewayDevice.WANDevice.' \
  --data-urlencode 'limit=100' --data-urlencode 'offset=0' \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/catalog" \
  | jq '{has_more,items:[.items[]|{path,is_object,writable,observed_at,operation_id}]}'
```

Aumente `offset` de 100 em 100 enquanto `has_more=true`. Procure em arquivo local,
se desejar, nomes com `Optical|PON|GPON|RXPower|TXPower`; são pistas, não prova de
semântica nem de unidade. Navegue também por ramos WAN que o catálogo revelar.
Só use caminhos folha (`is_object=false`) realmente retornados. A política GPN
retém uma seleção segura de folhas e objetos estruturais; o catálogo não representa
a árvore inteira da CPE. Ausência no catálogo parcial não prova ausência no aparelho.

## 4. Ler folhas revisadas em lotes controlados

O POST de GPV aceita `parameter_names` para selecionar até 16 folhas nos escopos
`device_info`, `wan` e `optical`. Em WAN/óptica, cada folha precisa constar numa
descoberta bem-sucedida da mesma CPE/firmware nas últimas 24 horas e corresponder
à lista permitida pelo perfil. A chamada falha em 409 se faltar descoberta ou a
política não permitir o caminho. Execute um lote por vez; cada operação usa uma
nova sessão CWMP. A divisão dos lotes é manual neste fluxo WAN/óptico.

Após confirmar no catálogo o **objeto óptico exato** aceito pelo perfil e suas
folhas, substitua os placeholders por caminhos completos reais:

```bash
export OPTICAL_OBJECT='OBJETO_OPTICO_CONCRETO_DESCOBERTO_COM_PONTO_FINAL'
export OPTICAL_RX='FOLHA_RX_COMPLETA_DESCOBERTA'
export QUERY_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc --arg path "$OPTICAL_OBJECT" --arg name "$OPTICAL_RX" --arg key "$QUERY_KEY" \
    '{kind:"get_parameter_values",scope:"optical",parameter_path:$path,parameter_names:[$name],idempotency_key:$key,capture_evidence:true}')" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries" | jq .
```

Repita para TX com outra chave e folha comprovada, ou use as duas folhas no mesmo
array se ambas estiverem descobertas. A política óptica atual só reconhece
`RXPower` e `TXPower` no objeto `X_GponInterafceConfig.` com instância real de
`WANDevice`; outros caminhos retornam 409 até revisão explícita. Mesmo quando a
leitura retorna um número, a escala/unidade está **não verificada**. A operação
pode terminar `failed/unverified_units` e ainda ter `evidence_available=true`.

Para WAN PPPoE, selecione o **objeto concreto** descoberto de cada WAN e suas
folhas aprovadas. O perfil exige `TransportType` e `X_HW_SERVICELIST` no mesmo
lote para classificar a conexão de Internet. Acrescente somente outros caminhos
presentes no catálogo e permitidos, como `ExternalIPAddress`, `MACAddress`,
`DNSServers` ou `ConnectionStatus`; não use um índice WAN fixo:

```bash
export WAN_OBJECT='OBJETO_WANPPPCONNECTION_CONCRETO_COM_PONTO_FINAL'
export WAN_NAMES_JSON="$(jq -nc --arg p "$WAN_OBJECT" \
  '[$p+"TransportType",$p+"X_HW_SERVICELIST",$p+"ExternalIPAddress"]')"
export QUERY_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc --arg path "$WAN_OBJECT" --argjson names "$WAN_NAMES_JSON" --arg key "$QUERY_KEY" \
    '{kind:"get_parameter_values",scope:"wan",parameter_path:$path,parameter_names:$names,idempotency_key:$key,capture_evidence:true}')" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries" | jq .
```

Apenas se a resposta indicar PPPoE e serviço `INTERNET` os dados entram na
telemetria canônica. O perfil também aceita um caminho IPv6 específico quando
concretamente descoberto; consulte a [política atual](WAN_OPTICAL_TELEMETRY.md)
antes de incluí-lo. Gateway, prefixo delegado, VLAN, nome da conexão, estado PON
e demais serviços não têm mapeamento homologado nesta entrega: registre
`pendente` ou `indisponível`, sem inferir pelo nome. Para várias WANs, repita por
objeto descoberto e compare seus serviços; não escolha automaticamente a Internet
pela posição numérica.

## 5. Confirmar resultado novo e exportar evidências

O `id` retornado por cada POST identifica a nova operação. Consulte
`GET /api/v1/cpes/$CPE_ID/queries/$OP_ID` até estado terminal e compare `id`,
`created_at`, `finished_at`, `attempt_count` e `result`. `GET .../telemetry` mostra
a **última observação persistida**, que pode ser anterior à operação atual. Confira
`operation_id` e data de proveniência antes de chamar esse valor de leitura nova.
O endpoint de inventário e o catálogo também leem o banco, sem disparar RPCs.

```bash
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/telemetry" | jq .
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/$OP_ID/evidence" \
  --output "query-$OP_ID.json"
UV_CACHE_DIR=/tmp/acs-uv-cache uv run --no-sync acs-evidence verify-query "query-$OP_ID.json"
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/export?format=json" \
  --output resultados.json
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/export?format=xml" \
  --output respostas.xml
```

`/evidence` só funciona se `capture_evidence=true` e houve resposta correlacionada.
Seu XML é **reconstruído**, não captura bruta. O pacote óptico conserva o número
bruto e tipo resolvido, com unidade `unverified`. O pacote WAN substitui
endereços/MACs. A exportação agregada mostra catálogo, escopos, falhas, tipos
persistidos e valores permitidos; valores antigos sem tipo ficam `omitted`, e
ela não recupera o número óptico bruto. Por isso **forneça os arquivos
`query-*.json` da leitura óptica/WAN e `resultados.json`** na próxima etapa.
Revise qualquer arquivo antes de compartilhar: caminhos podem conter
identificadores criados pelo fabricante. Não inclua credenciais nem XML bruto.

## 6. Comparar com a interface da ONU e preparar perfil

No mesmo horário aproximado, anote na interface da ONU: RX/TX **com unidade**,
estado PON, identificação da WAN de Internet e das demais WANs, IPv4/IPv6,
prefixo, gateway, DNS, VLAN e serviço. Compare cada leitura CWMP com a tela e
registe divergências/ausências. A comparação é necessária para confirmar escala e
unidade óptica; uma divisão por 100/1000 não pode ser presumida.

O rascunho atual contém apenas modelo, firmware e hardware comprovados.
[Perfil X6](HUAWEI_X6_DRAFT_FROM_EXPORT.md). A próxima revisão deverá registrar
por campo: caminho concreto ou regra que resolve instâncias, tipo XML observado,
conversão/unidade verificada, fonte da evidência e situação `comprovado` ou
`pendente`. Regras de WAN de Internet deverão usar classificação por serviço e
tipo, não índices fixos. Mapeamentos Huawei continuam em `acs.cwmp.compatibility`;
o parser SOAP genérico em `acs.cwmp.soap` não recebe lógica de fabricante.
Nenhuma aprovação é automática.

## Valores reais para comparação local

Ao solicitar GPV de WAN ou óptica com `capture_evidence: true`, a operação
passa a guardar também observações privadas dos campos permitidos. Consulte com
uma conta administradora, substituindo o identificador da operação:

```bash
curl --cacert "$ACS_CA" --user "$ACS_ADMIN_USER" \
  "$ACS_API_URL/api/v1/cpes/$CPE_ID/queries/$OP_ID/private-observations"
```

A resposta contém caminho, tipo XML e valor real, inclusive quando a operação
não classifica a WAN. Valores ainda não interpretados são preservados para
comparação; sua presença não confirma tipo, significado ou unidade. Campos de
credenciais continuam excluídos. Os dados ficam no PostgreSQL até a operação ser removida; restrinja o
acesso ao banco e não compartilhe essa resposta. As exportações e evidências
continuam anonimizadas. Operações anteriores não podem recuperar valores
substituídos: agende uma nova leitura depois da migration e reinicie a API e o
receptor CWMP para carregar a versão atualizada.

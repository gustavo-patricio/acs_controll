# Evidências de consultas CWMP

Este incremento permite agendar uma leitura e guardar sua evidência no PostgreSQL
para análise offline. O receptor processa a operação quando a CPE abre uma sessão;
não depende de uma conversa aberta com o assistente. API, receptor e banco precisam
estar em execução. Não há varredura automática nem reinicialização da CPE.

## Solicitar e exportar

Em `POST /api/v1/cpes/{cpe_id}/queries`, acrescente `capture_evidence: true`:

```json
{
  "kind": "get_parameter_names",
  "scope": "discovery",
  "parameter_path": "InternetGatewayDevice.WANDevice.",
  "idempotency_key": "c27f86bd-ff93-4582-b094-c516a96029c0",
  "capture_evidence": true
}
```

Use um UUID novo para cada intenção. A chave inclui o tipo, escopo, caminho e
opção de captura; alterar essa opção com a mesma chave retorna conflito.
O padrão é `false`. Consultas anteriores não recebem evidência retroativamente.

Consulte `GET /api/v1/cpes/{cpe_id}/queries/{operation_id}`. Quando
`evidence_available` for `true`, baixe:

```text
GET /api/v1/cpes/{cpe_id}/queries/{operation_id}/evidence
```

A exportação é um anexo JSON com `Cache-Control: no-store`, documentado no Swagger.
Exige HTTP Basic sobre HTTPS e permissão `cpe:query` (administrador), inclusive
quando o inventário anônimo de laboratório está habilitado. Ausência de autorização
retorna 401/403; operação inexistente ou de outra CPE, 404; evidência ainda ausente,
409; indisponibilidade do armazenamento, 503.

Na CLI local de laboratório, `acs-query` aceita `--capture-evidence`.
Para verificar o arquivo exportado, sem rede, banco ou CPE:

```bash
uv run acs-evidence verify-query ./query-evidence.json
```

## Contrato e decisões

O formato `query-evidence-1` contém manifesto, XML de solicitação e de resposta,
SHA-256 e tamanho de cada XML. Guarda perfil/versão, identidade técnica do modelo,
firmware, escopo, caminhos, horário de recebimento no servidor e campos omitidos
ou substituídos. Preserva os tipos XML resolvidos (`xsd:int`, por exemplo).

Os envelopes são **reconstruídos**, não cópias dos bytes originais. IDs de sessão,
RPC e equipamento não são exportados; IPs, DNS e MACs válidos são substituídos por
exemplos. Credenciais não pertencem aos caminhos de leitura permitidos. Textos de
fault são removidos. Valores inválidos são omitidos. A descoberta mantém somente
nomes aprovados e registra a quantidade recebida antes do filtro. Todo pacote é
explicitamente parcial; não representa a árvore inteira do equipamento.

Valores ópticos numéricos brutos são preservados, mas a unidade continua
`unverified`. Capturar `RXPower` não homologa sua escala. Uma operação óptica pode
terminar em falha de normalização e ainda fornecer evidência útil. Faults
correlacionados também podem gerar pacote; respostas não correlacionadas,
malformadas ou com parâmetros inesperados não geram evidência.

A evidência é gravada na mesma transação da conclusão da operação, limitada a
1 MiB. Repetições válidas preservam o pacote original. A migration
`0008_query_evidence` adiciona a opção e o conteúdo à operação; não cria outro
sistema de jobs. A aplicação usa a interface de política; XML e regras Huawei
permanecem no adaptador CWMP.

O verificador rejeita alterações inconsistentes de XML, hashes, manifesto e
anonimização. Hashes não são assinatura nem comprovam origem: alguém pode criar
outro pacote inteiramente coerente. A verificação usa as políticas suportadas pela
versão instalada do ACS; mudanças futuras precisam preservar esse contrato.

## Limites e próximo incremento

Continuam valendo uma operação por sessão, uma operação ativa por CPE e os prazos
de [consultas](READ_QUERIES.md). GPN usa descoberta por nível; GPV WAN/óptico exige
descoberta recente e caminhos permitidos. O armazenamento ainda não tem política
automática de retenção. Não há encadeamento automático GPN → GPV, importação de
perfil declarativo ou homologação automática por nome de parâmetro.

A próxima entrega pode usar estes arquivos como fixtures offline para revisar
caminhos IPv4/IPv6 e confirmar unidades ópticas por modelo/firmware. Os valores
normalizados continuam em `/telemetry`; `/inventory` contém identificação e
capacidades. A exportação de evidência não altera essa separação.

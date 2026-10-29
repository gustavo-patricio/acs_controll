# Exportação de resultados armazenados

A API administrativa exporta um retrato dos dados já persistidos para uma CPE.
Não agenda RPC, não consulta a CPE e não lê XML bruto. Exige HTTP Basic sobre
HTTPS e permissão `cpe:query` (`administrator`). O modo de inventário anônimo não
libera esta rota.

```text
GET /api/v1/cpes/{cpe_id}/queries/export?format=json&prefix=InternetGatewayDevice.DeviceInfo.
GET /api/v1/cpes/{cpe_id}/queries/export?format=xml&prefix=InternetGatewayDevice.DeviceInfo.
```

No diretório `backend`, com `ACS_ADMIN_USER` e `CPE_ID` configurados:

```bash
curl --cacert .tls/server.crt --user "$ACS_ADMIN_USER" \
  --output resultados.json \
  "https://localhost:8000/api/v1/cpes/$CPE_ID/queries/export?format=json"

curl --cacert .tls/server.crt --user "$ACS_ADMIN_USER" \
  --output respostas.xml \
  "https://localhost:8000/api/v1/cpes/$CPE_ID/queries/export?format=xml"
```

O curl solicita a senha de forma interativa. `prefix` é literal (inclusive `%` e
`_`) e filtra catálogo/últimos valores por caminho. Não filtra o histórico recente
de operações; este fornece escopo e falhas para interpretar o retrato. `format`
aceita `json` (padrão) ou `xml`. O endpoint figura em `/docs` e retorna anexo com
`Cache-Control: no-store`. `401` indica autenticação ausente, `403` permissão ou
transporte inadequado, `404` CPE inexistente, `422` parâmetro inválido e `409`
prefixo amplo demais para o limite de exportação. `503` indica banco indisponível.

## Conteúdo e fidelidade

O JSON `stored-results-1` contém identidade técnica (fabricante, modelo,
ProductClass, firmware, serial sintético), escopo, catálogo GPN, últimas observações
GPV tipadas e operações. Cada entrada tem uma referência anônima à operação de
origem. As operações trazem método, escopo, estado e motivo/código de falha.
`history_truncated` informa se existem operações além das 256 mais recentes;
operações necessárias para atribuir observações presentes são incluídas mesmo
quando mais antigas. São permitidas até 4096 entradas no catálogo e 4096 valores,
512 operações e 8 MiB por exportação. Use um prefixo mais estreito se exceder.

O XML é um arquivo `CollectedResponses`, com manifesto JSON anonimizado e envelopes
SOAP separados por operação. O atributo `fidelity="reconstructed"` identifica que
foi produzido do banco, não recebido da CPE. Cada resposta é marcada `partial`:
catálogo e valores conservam apenas a observação mais recente por caminho, e
respostas antigas podem não ter tipo/valor recuperável. O manifesto indica omissões.
Faults usam apenas o código persistido; o texto original é substituído. Não se
inventam tipos XML perdidos. As strings vazias que passaram pela política são
preservadas no JSON e no XML reconstruído.

## Política de anonimização

- O serial vira `ANONYMIZED0001`, também quando aparece em um valor ou caminho.
- Endereços IPv4/IPv6 são substituídos individualmente por endereços reservados
  para exemplo; repetições do mesmo endereço usam o mesmo substituto no arquivo.
  MACs são substituídos individualmente por endereços locais sintéticos.
- URLs individualizadas viram `https://example.invalid/anonymized`.
- Caminhos que indicam senha, token, usuário ou credencial nunca exportam valor.
  Valores proprietários (`X_...`) com sensibilidade desconhecida também são
  omitidos. O caminho, tipo reportado, motivo `omitted` e referência da operação
  ficam disponíveis para análise sem expor o valor.
- Para valores normais, só há publicação de caminhos DeviceInfo revisados e de
  alguns campos de rede tratados acima. Outros valores recebem
  `not_in_export_allowlist`. A exportação não altera a política de coleta.

Os caminhos são preservados literalmente, exceto se contiverem o serial conhecido.
Uma hierarquia construída pela própria CPE com identificador individual desconhecido
não pode ser identificada com segurança só pelo banco; trate o arquivo como material
administrativo e revise caminhos antes de compartilhá-lo externamente. Hashes de
senha, credenciais e XML original não são lidos pela exportação. O banco retém os
valores conforme a política de persistência anterior; esta exportação não apaga ou
modifica esses dados.

Teste focado:

```bash
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q \
  tests/unit/test_result_export_policy.py tests/integration/test_result_export.py
```

# Coleta controlada de Inform para compatibilidade

## Objetivo e limites

A coleta produz uma fixture XML anonimizada a partir de **um Inform autenticado,
autorizado e persistido** de uma CPE previamente registrada. Serve para estudar os
caminhos básicos de inventário efetivamente enviados pelo equipamento. Não envia
comandos à CPE, não acrescenta RPCs e não altera o contrato da API ou o banco.

O coletor é desativado por padrão. A ativação exige o UUID da CPE no inventário e
um caminho absoluto de saída. A janela é de 300 segundos por padrão, com limite
de 600 segundos, contados a partir da inicialização do processo. Após uma tentativa
de gravação para a CPE selecionada, a coleta termina; o receptor continua operando.
Outras CPEs não consomem essa tentativa. Use um processo, sem `--reload`.

Reiniciar com as mesmas opções inicia outra janela. Um arquivo já existente nunca
é substituído, mesmo entre processos. Não mantenha as opções de coleta no `.env`.
O receptor mantém suas exigências de TLS e autenticação, inclusive quando usa uma
conta compartilhada. Nesse caso, o UUID seleciona a identidade declarada e
persistida, mas a conta compartilhada não comprova exclusividade física da CPE.

## O que é preservado

- `DeviceId.Manufacturer`, `OUI` e `ProductClass`, necessários à identificação do
  produto; o OUI não é um endereço MAC completo.
- Os caminhos exatos `Device.DeviceInfo.ModelName`, `SoftwareVersion` e
  `HardwareVersion`, e seus equivalentes sob `InternetGatewayDevice`.
- Os textos desses parâmetros, sem normalização, desde que tenham no máximo
  256 caracteres imprimíveis. Campos vazios permanecem vazios, conflitos entre
  raízes permanecem visíveis e campos ausentes não são inventados.
- Um comentário com versão do formato de coleta, horário UTC do recebimento no
  ACS e quantidades de parâmetros recebidos e retidos.

Serial, ID do RPC, eventos, `CommandKey`, horário da CPE e contadores são
substituídos por dados sintéticos. Os tipos são reconstruídos como `xsd:string`:
o parser atual não preserva o contexto dos prefixos XML do tipo original.
Os demais caminhos **e seus valores** são descartados, incluindo credenciais,
PPPoE, endereços, URLs de Connection Request e extensões proprietárias.
Textos de identificação não imprimíveis são substituídos por `REDACTED`.

O arquivo é uma reconstrução parcial, não uma captura do envelope original.
Não comprova detalhes de serialização, tipos XML, eventos ou suporte a RPCs.
Um parâmetro ausente na fixture pode não ter sido enviado ou ter sido excluído
pela regra de tamanho/caracteres. Isso não comprova ausência de suporte.
A lista permitida reduz a exposição, mas revise os textos preservados antes de
versionar ou compartilhar a fixture: o conteúdo desses campos é declarado pela CPE.

## Como executar

Obtenha o `id` em `GET /api/v1/cpes` e confira o produto e firmware da bancada.
Encerre o receptor atual com `Ctrl+C` no terminal que ocupa a porta 7547. Dentro
de `backend/`, inicie uma instância com as opções temporárias:

```bash
ACS_CWMP_CAPTURE_CPE_ID="UUID-DA-CPE-NO-INVENTARIO" \
ACS_CWMP_CAPTURE_OUTPUT="$HOME/.local/share/acs/.captures/inform.xml" \
ACS_CWMP_CAPTURE_SECONDS=300 \
uv run uvicorn acs.cwmp.http.app:app \
  --host 0.0.0.0 --port 7547 --no-access-log --no-proxy-headers \
  --ssl-certfile .tls/mirrored/server.crt \
  --ssl-keyfile .tls/mirrored/server.key
```

Use o filesystem Linux, não `/mnt/c` ou `/mnt/d`. O coletor cria o diretório de
saída com modo `0700` e recusa diretório existente com outra permissão. O arquivo
é criado com modo `0600`. A publicação é atômica e não segue um link simbólico
existente no destino. Somente os bytes já anonimizados chegam ao arquivo temporário.

Mantenha a URL e as credenciais atuais da CPE e aguarde o próximo Inform periódico.
Não é necessário reiniciar nem reconfigurar o equipamento. O receptor registra
um evento JSON `inform_capture` correlacionado por `session_id` e `cpe_id`:

| `outcome` | Significado |
| --- | --- |
| `saved` | Fixture publicada com sucesso |
| `already_exists` | Destino existente preservado; nenhuma nova fixture foi salva |
| `expired` | O Inform chegou após o prazo; nenhuma fixture foi salva |
| `failed` | Falha na coleta; o Inform e a sessão continuam normalmente |

Esses eventos não incluem parâmetros, credenciais, serial, caminho de saída ou
mensagens de exceção. A expiração é verificada na chegada de um Inform da CPE
selecionada; sem tráfego dessa CPE, não haverá evento de expiração.

Confira localmente o arquivo, sem publicá-lo integralmente em logs:

```bash
ls -l "$HOME/.local/share/acs/.captures/inform.xml"
```

Depois, reinicie o receptor com o comando habitual, sem as três variáveis.
A coleta termina sozinha, mas remover as opções evita reativá-la em outro início.
Se precisar repetir, escolha um novo nome de arquivo e inicie outra janela.

## Promoção para fixture e próxima entrega

Arquivos locais em `.captures/` são ignorados pelo Git. Após revisão, uma fixture
poderá ser copiada explicitamente para `tests/fixtures/cwmp/` com documentação da
origem, produto, firmware, data e regras de anonimização. A captura não é promovida
automaticamente nem representa homologação de toda a linha Huawei.

Esta coleta prepara a etapa 3 do plano de normalização. A implementação e a
seleção do perfil específico dependem da revisão da evidência obtida.

Em 25/10/2026 foi realizada e revisada a primeira coleta da Huawei EG8145X6-10,
firmware V5R022C00S364. A [fixture e sua origem](../tests/fixtures/cwmp/huawei_eg8145x6_10_v5r022c00s364.md)
registram hardware e firmware sob `InternetGatewayDevice.DeviceInfo`. A sessão
real concluiu com HTTP 200/204. O [primeiro perfil Huawei](HUAWEI_PROFILE.md)
foi implementado a partir dessa evidência, com seleção restrita ao produto/firmware.

## Validação

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests/unit/test_inform_capture.py tests/contract/test_cwmp_capture.py
ACS_RUN_INTEGRATION_TESTS=1 uv run pytest -q
```

Os testes cobrem exclusão de dados sensíveis, reconstrução parseável, seleção da
CPE, prazo, permissões, concorrência, destino existente, falha de gravação,
autenticação, autorização, XML inválido/XXE, rejeição do banco e o fluxo completo
Inform/InformResponse/POST vazio em PostgreSQL com migrations em schema descartável.

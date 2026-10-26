# Plano original: consultas remotas por perfil

**Atualização:** a primeira entrega de `GetParameterNames` e `GetParameterValues`
está implementada. Veja [escopo, comandos e limitações atuais](READ_QUERIES.md).
O texto abaixo preserva o plano original. A implementação limita-se a uma operação
por sessão, sem encadeamento automático, para três campos DeviceInfo do perfil Huawei.

## Avaliação

Os dois RPCs são adequados para ampliar a coleta: `GetParameterNames` descobre
nomes e informações de acesso; `GetParameterValues` obtém valores. A referência
do protocolo adotado é [TR-069 de maio de 2004, seções A.3.2.2 e A.3.2.3](https://www.broadband-forum.org/pdfs/tr-069-1-0-0.pdf).
Os elementos XML constam do [schema oficial CWMP 1.0](https://github.com/BroadbandForum/cwmp-data-models/blob/master/cwmp-1-0.xsd).

Recomendação do projeto: começar por valores conhecidos de `DeviceInfo` e usar
descoberta delimitada para caminhos ou instâncias ainda desconhecidos. Descobrir
um nome não comprova que o ACS interprete corretamente seu valor. O resultado
precisa passar pelo perfil, pela lista permitida e pela validação de tipos/unidades.

O objetivo inicial será consultar modelo, firmware e hardware por caminhos
explícitos. Depois, com fixture real, acrescentar uptime em segundos e objetos
WAN/Wi-Fi/óptica conforme a evidência de cada modelo e firmware. Os campos
proprietários Huawei não serão presumidos por nome comercial.

## Fluxo pretendido

1. Um usuário autorizado solicita uma coleta por perfil pela API; recebe o ID de
   uma operação persistida e pode acompanhar seu estado.
2. A operação aguarda a próxima sessão periódica da CPE. Nesta primeira versão,
   não dependerá de Connection Request.
3. O receptor aceita o Inform e devolve InformResponse como hoje.
4. No POST vazio, havendo uma consulta pendente, o ACS envia o RPC correspondente.
5. A CPE envia a resposta; o ACS correlaciona sessão, operação e `cwmp:ID`, valida
   o conteúdo e persiste o resultado. Se houver outra consulta autorizada, a
   sessão continua; ao esgotar a fila, é encerrada normalmente.
6. Os resultados permitidos são normalizados e alimentam o inventário e seus
   metadados, para leitura pelo frontend.

Esse fluxo era o desenho proposto. A entrega atual envia a operação pendente no
POST vazio e encerra com 204 após a resposta; a próxima operação usa outra sessão.

## Escopo técnico da implementação

- Domínio: operação de leitura, tentativa, prazo e estados `pending`,
  `dispatched`, `succeeded`, `failed` e `expired`.
- Aplicação: solicitação autorizada, seleção de consulta por perfil, despacho,
  correlação, tratamento idempotente de respostas e atualização do inventário.
- CWMP: serializers dos dois pedidos, parsers protegidos das respostas/faults e
  estado de sessão que aceite uma resposta esperada por vez.
- PostgreSQL: operações e tentativas auditáveis, vínculo com CPE e sessão,
  prevenção de despacho duplicado e migrations.
- API: criação autenticada de operação e consulta de seu resultado. A exceção
  de leitura anônima do laboratório não autorizará disparo de operações.

Será necessário preservar a distinção entre origem `Inform` e consulta remota
nos metadados. Uma resposta parcial de consulta não deve ser simulada como Inform
para selecionar um perfil: a operação deverá carregar o perfil e a versão usados,
além de reavaliar resultados que revelem mudança de firmware.

## Limites propostos e validação

Como política inicial do projeto, usar uma consulta em andamento por CPE, lotes
pequenos de caminhos explícitos e prazo de expiração persistido. A descoberta
deverá limitar subárvore, profundidade, quantidade de nomes e tamanho de resposta.
`NextLevel` permite controlar o nível explorado; caminhos parciais terminam em
ponto, conforme a especificação citada. Não consultar a árvore inteira nem
transformar todos os nomes descobertos em pedidos de valores.

Credenciais, PPPoE e outros dados sensíveis permanecem excluídos. O parser deverá
rejeitar XXE e estruturas excessivas. Respostas fora da operação/sessão esperada,
IDs incorretos, duplicatas, timeouts e faults precisam de testes próprios.

No CWMP 1.0, a especificação define para `GetParameterValues` os faults
`9001`, `9002`, `9003`, `9004`, `9005`; para `GetParameterNames`, `9001`, `9002`,
`9003`, `9005`. `9005` identifica nome de parâmetro inválido. Não criar códigos
SOAP para estados internos como expiração da operação. Conferir esses conjuntos
na referência normativa antes de implementar o tratamento.

Critérios de aceite: testes de parser/serializer, transições de sessão, repetição,
concorrência, expiração, autorização, exclusão de segredos e integração PostgreSQL.
Depois, validar consultas somente de leitura na CPE de bancada e guardar fixtures
anonimizadas das respostas. O suporte a capacidades deverá ser declarado somente
com essas evidências e seus mapeamentos testados.

## Limitação do significado de status

Uma resposta recente fornece uma observação recente, não uma garantia permanente
de conectividade. Classificar online/offline ainda exige a janela configurável
prevista em RN-002. Uptime, estado WAN e potência óptica são informações diferentes
e terão campos, unidades e horários próprios; não serão condensados em um único
estado inferido do nome do perfil.

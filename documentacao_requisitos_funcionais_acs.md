# ACS para Gerenciamento de CPEs Huawei

## Especificação Inicial de Requisitos Funcionais

| Campo | Valor |
| --- | --- |
| Projeto | ACS para gerenciamento de CPEs Huawei |
| Documento | Especificação inicial de requisitos funcionais |
| Versão | 0.1 |
| Estado | Em elaboração |
| Origem | Análise funcional das interfaces do sistema atualmente utilizado |
| Protocolo principal previsto | TR-069/CWMP |

## 1. Objetivo do documento

Este documento registra os requisitos funcionais inicialmente identificados para o desenvolvimento de um Auto-Configuration Server (ACS) próprio. A solução será utilizada para inventariar, consultar, monitorar e, em etapas posteriores, configurar remotamente CPEs Huawei instaladas nos clientes da empresa.

Os requisitos foram extraídos pela observação do comportamento aparente das interfaces do sistema atualmente utilizado. As telas servem como referência para identificar necessidades operacionais, não como especificação de código, identidade visual ou reprodução da solução existente.

## 2. Escopo desta versão

Esta versão cobre as funcionalidades observadas nas seguintes áreas:

- painel operacional;
- inventário e pesquisa de CPEs;
- consulta detalhada de dispositivos;
- conectividade WAN;
- redes Wi-Fi e hosts conectados;
- diagnósticos;
- históricos e eventos;
- usuários e grupos de acesso;
- administração do ACS.

As operações de alteração remota de parâmetros ainda não estão completamente especificadas, pois não foram fornecidas telas suficientes desses fluxos.

## 3. Fontes analisadas

| Fonte | Conteúdo observado |
| --- | --- |
| `dispositivos.png` | Inventário, pesquisa, filtros, colunas, exportação e paginação |
| `home.png` | Indicadores operacionais, status, potência óptica, utilização e resets |
| `interface_gerenciamento_dispositivo.png` | Resumo da CPE, WAN, Wi-Fi, hosts, diagnósticos e histórico |
| `interface_recursos_sistema.png` | Backups, arquivos, atualizações, configurações, logs e servidor |
| `interface_usuario.png` | Perfil, idioma, tema, configurações e encerramento de sessão |
| `interface_usuarios.png` | Cadastro, pesquisa, grupos, usuários online e exclusão |

Dados pessoais e operacionais presentes nas imagens não fazem parte desta documentação.

## 4. Glossário

| Termo | Definição |
| --- | --- |
| ACS | Auto-Configuration Server responsável pelo gerenciamento remoto dos equipamentos |
| CPE | Equipamento instalado no ambiente do cliente, como modem, roteador ou ONT |
| CWMP | CPE WAN Management Protocol, definido pelo TR-069 |
| TR-069 | Protocolo utilizado na comunicação de gerenciamento entre a CPE e o ACS |
| WAN | Interface de comunicação da CPE com a rede da operadora |
| SSID | Nome de uma rede Wi-Fi |
| PPPoE | Protocolo frequentemente utilizado para autenticação do acesso do cliente |
| RBAC | Controle de acesso baseado em papéis ou grupos de usuários |
| MVP | Primeira versão utilizável, contendo apenas as capacidades essenciais |

## 5. Atores identificados

| Ator | Responsabilidades esperadas |
| --- | --- |
| Administrador | Gerenciar usuários, grupos, configurações, arquivos, backups e o ambiente ACS |
| Atendimento | Localizar clientes e CPEs, consultar conectividade e executar ações autorizadas |
| NOC/Engenharia | Monitorar indicadores, investigar eventos e executar diagnósticos técnicos |
| CPE Huawei | Iniciar sessões TR-069, informar dados e executar operações solicitadas pelo ACS |
| Sistema externo | Consultar ou fornecer dados por integração; participação ainda pendente de confirmação |

## 6. Classificação de prioridade

| Prioridade | Significado |
| --- | --- |
| P0 | Obrigatório para o MVP ou para a operação segura do núcleo do ACS |
| P1 | Importante após a estabilização do núcleo funcional |
| P2 | Complementar; pode ser planejado para versões posteriores |

## 7. Requisitos funcionais

### 7.1 Acesso e usuários

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-001 | O sistema deverá autenticar os usuários antes de permitir acesso ao ACS. | P0 |
| RF-002 | O sistema deverá permitir que o usuário encerre sua sessão. | P0 |
| RF-003 | O administrador deverá cadastrar novos usuários. | P0 |
| RF-004 | O administrador deverá pesquisar usuários por nome. | P0 |
| RF-005 | O administrador deverá visualizar nome, e-mail, estado e grupo de cada usuário. | P0 |
| RF-006 | O administrador deverá associar usuários a grupos de acesso, incluindo inicialmente Administrador e Atendimento. | P0 |
| RF-007 | O administrador deverá desativar ou remover usuários. | P0 |
| RF-008 | O sistema deverá informar quais usuários estão online. | P1 |
| RF-009 | O sistema deverá permitir exportar a relação de usuários conforme os filtros aplicados. | P2 |
| RF-010 | O usuário deverá editar os dados permitidos de seu próprio perfil. | P1 |
| RF-011 | O usuário poderá selecionar o idioma e o tema da interface. | P2 |

### 7.2 Inventário de CPEs

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-012 | O sistema deverá registrar automaticamente uma CPE quando ela se comunicar com o ACS. | P0 |
| RF-013 | O sistema deverá manter um inventário persistente dos equipamentos conhecidos. | P0 |
| RF-014 | O sistema deverá apresentar o estado operacional da CPE, diferenciando equipamentos online e offline. | P0 |
| RF-015 | O sistema deverá identificar a CPE pelo fabricante, modelo e número de série. | P0 |
| RF-016 | O sistema deverá registrar os endereços IPv4 e IPv6 associados à CPE. | P0 |
| RF-017 | O sistema deverá registrar as versões de firmware e hardware informadas pela CPE. | P0 |
| RF-018 | O sistema deverá registrar a data da primeira comunicação da CPE com o ACS. | P0 |
| RF-019 | O sistema deverá registrar a data e a hora da última comunicação da CPE. | P0 |
| RF-020 | O sistema deverá registrar a data da última pré-configuração realizada. | P1 |
| RF-021 | O sistema deverá coletar as leituras ópticas de transmissão e recepção disponibilizadas pela CPE. | P0 |
| RF-022 | O sistema deverá permitir associar uma descrição à CPE. | P1 |
| RF-023 | O sistema deverá permitir adicionar e remover etiquetas da CPE. | P1 |

#### Modelos inicialmente identificados

- Huawei EG8145V5-V2;
- Huawei EG8145X6-10;
- Huawei EG8145V5;
- Huawei EG8245W5-6T;
- Huawei EG8245H;
- Huawei HG8121H;
- Huawei V166a-20.

Os modelos e suas versões de firmware deverão formar uma matriz de compatibilidade. A presença na lista não confirma que todos ofereçam os mesmos parâmetros ou operações TR-069.

### 7.3 Pesquisa e visualização do inventário

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-024 | O sistema deverá permitir pesquisar CPEs por login, serial, endereço IP, fabricante, modelo e outros campos definidos. | P0 |
| RF-025 | O sistema deverá permitir a combinação de filtros na relação de CPEs. | P0 |
| RF-026 | O sistema deverá apresentar os resultados de forma paginada. | P0 |
| RF-027 | O sistema deverá informar a quantidade total de dispositivos encontrados. | P0 |
| RF-028 | O usuário deverá abrir os detalhes de uma CPE a partir do inventário. | P0 |
| RF-029 | O usuário deverá escolher quais colunas serão exibidas no inventário. | P1 |
| RF-030 | O usuário poderá reorganizar as colunas da listagem. | P2 |
| RF-031 | O usuário poderá escolher a densidade de apresentação das linhas. | P2 |
| RF-032 | O sistema deverá permitir exportar o inventário conforme os filtros aplicados. | P1 |
| RF-033 | O usuário poderá salvar visualizações ou conjuntos de filtros frequentes. | P2 |
| RF-034 | O usuário poderá marcar CPEs como favoritas. | P2 |

### 7.4 Consulta detalhada da CPE

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-035 | O sistema deverá disponibilizar uma página individual para cada CPE. | P0 |
| RF-036 | A página deverá apresentar modelo, descrição, serial, firmware, hardware, endereços IP e última comunicação. | P0 |
| RF-037 | O usuário deverá solicitar a atualização das informações apresentadas. | P0 |
| RF-038 | O sistema deverá apresentar o uptime informado pela CPE. | P0 |
| RF-039 | O sistema deverá mostrar o estado das portas físicas do equipamento. | P1 |
| RF-040 | O sistema deverá mostrar valores atuais e histórico das leituras ópticas de transmissão e recepção. | P1 |
| RF-041 | O sistema deverá organizar as informações da CPE em resumo, conectividade, diagnósticos, arquivos, propriedades e logs. | P1 |
| RF-042 | O sistema deverá permitir copiar campos técnicos, como serial e endereços IP, respeitando as permissões do usuário. | P2 |

### 7.5 Conectividade WAN

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-043 | O sistema deverá listar as interfaces WAN configuradas na CPE. | P0 |
| RF-044 | O sistema deverá diferenciar interfaces PPP e interfaces IP. | P0 |
| RF-045 | O sistema deverá informar o estado de conexão de cada interface WAN. | P0 |
| RF-046 | O sistema deverá apresentar, quando disponíveis, usuário PPPoE, IPv4, IPv6, gateway, DNS e VLAN. | P0 |
| RF-047 | O sistema deverá informar se o IPv6 está habilitado. | P1 |
| RF-048 | O sistema deverá suportar a apresentação de múltiplas interfaces WAN por CPE. | P0 |

### 7.6 Redes Wi-Fi e hosts conectados

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-049 | O sistema deverá listar as redes Wi-Fi configuradas na CPE. | P0 |
| RF-050 | O sistema deverá diferenciar as redes de 2,4 GHz e 5 GHz. | P0 |
| RF-051 | O sistema deverá apresentar SSID, estado, canal e quantidade de hosts de cada rede. | P0 |
| RF-052 | O sistema deverá informar se a seleção automática de canal está habilitada. | P1 |
| RF-053 | O sistema deverá identificar redes usadas como backhaul ou EasyMesh, quando suportadas. | P1 |
| RF-054 | O sistema deverá listar os hosts conectados à rede local da CPE. | P0 |
| RF-055 | Para cada host, o sistema deverá mostrar nome, IP local, banda e qualidade do sinal, quando disponíveis. | P1 |
| RF-056 | O sistema poderá apresentar uma avaliação agregada da qualidade do Wi-Fi. | P2 |

### 7.7 Diagnósticos

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-057 | O usuário autorizado deverá executar teste de ping a partir da CPE. | P1 |
| RF-058 | O usuário autorizado deverá executar traceroute a partir da CPE. | P1 |
| RF-059 | O usuário autorizado poderá solicitar uma varredura das redes Wi-Fi próximas. | P1 |
| RF-060 | O usuário autorizado poderá executar teste de velocidade quando o modelo e o firmware oferecerem suporte. | P2 |
| RF-061 | O sistema deverá registrar solicitante, dispositivo, parâmetros, resultado e duração de cada diagnóstico. | P1 |
| RF-062 | O sistema deverá informar quando um diagnóstico não for suportado pelo modelo ou firmware da CPE. | P0 |

### 7.8 Histórico, eventos e consumo

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-063 | O sistema deverá armazenar os eventos recebidos ou detectados para cada CPE. | P0 |
| RF-064 | O sistema deverá apresentar uma linha do tempo dos eventos da CPE. | P1 |
| RF-065 | O sistema deverá apresentar histórico de download e upload por período, quando esses dados estiverem disponíveis. | P1 |
| RF-066 | O usuário deverá alternar entre a visualização de consumo e a visualização de eventos. | P1 |
| RF-067 | O sistema deverá registrar as reinicializações detectadas em cada CPE. | P1 |

### 7.9 Painel operacional

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-068 | O sistema deverá exibir as quantidades total, online e offline de CPEs. | P1 |
| RF-069 | O sistema deverá apresentar o percentual de equipamentos online nas últimas 24 horas. | P1 |
| RF-070 | O sistema deverá calcular médias e distribuições das leituras ópticas de transmissão e recepção. | P1 |
| RF-071 | O painel deverá permitir alternar entre indicadores ópticos, Wi-Fi e IPv6. | P2 |
| RF-072 | O sistema deverá apresentar a quantidade de reinicializações no dia e seu histórico recente. | P1 |
| RF-073 | O sistema poderá apresentar os fabricantes e modelos mais consultados. | P2 |
| RF-074 | O sistema poderá apresentar indicadores de utilização por usuário. | P2 |
| RF-075 | O sistema poderá identificar anomalias nos indicadores monitorados. | P2 |
| RF-076 | O sistema poderá gerar resumos automáticos sobre a situação dos equipamentos e do ACS. | P2 |

### 7.10 Administração do sistema

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF-077 | O administrador deverá configurar os parâmetros gerais do ACS. | P0 |
| RF-078 | O administrador deverá cadastrar e gerenciar arquivos que poderão ser enviados às CPEs. | P1 |
| RF-079 | O sistema deverá manter nome, versão, fabricante, modelos compatíveis, tamanho e checksum dos arquivos destinados às CPEs. | P1 |
| RF-080 | O administrador deverá configurar e executar rotinas de backup. | P1 |
| RF-081 | O administrador deverá consultar os registros de alterações realizadas no sistema. | P0 |
| RF-082 | O administrador deverá consultar estatísticas operacionais do servidor ACS. | P1 |
| RF-083 | O sistema poderá informar quando houver atualização disponível para a própria aplicação. | P2 |

## 8. Regras de negócio inferidas

As regras desta seção são hipóteses produzidas a partir das telas e deverão ser validadas com os responsáveis pela operação.

| ID | Regra de negócio | Estado |
| --- | --- | --- |
| RN-001 | Cada CPE deverá possuir identificação única baseada na identidade informada pelo protocolo, incluindo fabricante/OUI, classe do produto e número de série. | A validar |
| RN-002 | A classificação online deverá considerar a última comunicação dentro de uma janela configurável. | A validar |
| RN-003 | As leituras ópticas deverão ser avaliadas por faixas configuráveis, possivelmente específicas por tecnologia ou modelo. | A validar |
| RN-004 | As operações disponíveis deverão depender do modelo e da versão de firmware da CPE. | A validar |
| RN-005 | Somente grupos autorizados poderão executar diagnósticos, enviar arquivos ou alterar configurações. | A validar |
| RN-006 | Toda operação remota deverá possuir um estado: pendente, em execução, concluída, falhou, expirou ou cancelada. | A validar |
| RN-007 | Toda ação administrativa ou remota deverá registrar usuário, dispositivo, data, operação e resultado. | A validar |
| RN-008 | Informações sensíveis não deverão aparecer integralmente em telas, exportações ou logs. | A validar |
| RN-009 | Arquivos de firmware ou configuração somente poderão ser enviados para modelos explicitamente compatíveis. | A validar |
| RN-010 | O sistema deverá diferenciar o último estado conhecido de uma informação obtida por leitura atualizada. | A validar |

## 9. Escopo funcional recomendado para o MVP

O MVP deverá priorizar:

1. autenticação e grupos de acesso;
2. registro automático das CPEs;
3. inventário, pesquisa e filtros;
4. classificação online e offline;
5. página de detalhes da CPE;
6. informações de conectividade WAN;
7. informações das redes Wi-Fi;
8. hosts conectados;
9. leituras de potência óptica;
10. histórico de sessões e eventos;
11. logs e auditoria;
12. compatibilidade com os modelos Huawei prioritários.

Os seguintes recursos deverão permanecer fora do primeiro MVP, salvo mudança de prioridade:

- resumo por inteligência artificial;
- pontuação agregada de Wi-Fi;
- ranking de utilização por usuário;
- favoritos e visualizações personalizadas;
- seleção de tema e múltiplos idiomas;
- atualização automática da própria aplicação.

## 10. Lacunas de requisitos

Ainda são necessárias evidências e entrevistas para especificar como o sistema deverá:

- alterar SSID e senha Wi-Fi;
- alterar parâmetros WAN e PPPoE;
- reiniciar uma CPE remotamente;
- restaurar uma CPE para as configurações de fábrica;
- enviar e acompanhar atualizações de firmware;
- criar templates ou perfis de configuração;
- realizar a pré-configuração de uma nova CPE;
- agendar e executar operações em lote;
- consultar e alterar a árvore de parâmetros TR-069;
- tratar faults e operações malsucedidas;
- definir as permissões de cada grupo;
- associar a CPE, o login PPPoE, o contrato e o cliente;
- solicitar conexão imediata à CPE por Connection Request;
- gerenciar credenciais utilizadas na comunicação TR-069;
- determinar a origem dos dados de consumo;
- calcular a pontuação de qualidade do Wi-Fi;
- definir precisamente os critérios de online e offline.

## 11. Telas e fluxos ainda necessários

Para evoluir esta especificação, deverão ser coletadas evidências das seguintes áreas:

1. aba **Conectividade** da CPE;
2. aba **Diagnósticos**, incluindo execução e resultado;
3. aba **Arquivos** e fluxo de envio de firmware;
4. aba **Propriedades**;
5. aba **Logs**;
6. menu de ações da CPE;
7. criação e edição de usuários;
8. criação de grupos e definição de permissões;
9. filtros e configurações da listagem;
10. templates ou pré-configurações;
11. execução de ações em lote;
12. telas de erro e confirmação de operações.

## 12. Questões para validação com a equipe

1. Qual intervalo sem comunicação faz uma CPE ser considerada offline?
2. A atualização manual executa uma Connection Request ou apenas consulta o banco de dados?
3. Quais modelos e firmwares Huawei representam a maior parte da base instalada?
4. Quais parâmetros são indispensáveis para o atendimento diário?
5. Quais ações o grupo Atendimento pode executar sem autorização do NOC?
6. O cadastro do cliente e do contrato virá de qual sistema?
7. Como uma CPE será associada ao cliente correto?
8. Os dados de consumo vêm da própria CPE ou de outra plataforma?
9. Quais limites ópticos devem gerar alerta para cada tipo de rede?
10. Quais operações precisam de aprovação adicional ou confirmação em duas etapas?

## 13. Controle de evolução

| Versão | Alteração |
| --- | --- |
| 0.1 | Criação da especificação inicial a partir das seis interfaces analisadas |


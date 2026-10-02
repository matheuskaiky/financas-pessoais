# Finanças Pessoais

Gestão financeira pessoal **local, privada e sob medida**. Você lança contas, compras no cartão (com parcelas e fatura), transferências e investimentos; o sistema calcula faturas, saldos, parcelamentos, patrimônio e totais, sem enviar nada a terceiros.

> **Status:** em desenvolvimento. As Fases 0 a 4 estão prontas (núcleo manual, cartões e investimentos: avaliações líquidas, aportes e resgates, rendimento, alocação, aplicações de renda fixa, escada de vencimentos, liquidez, exposição ao FGC, reserva de emergência e patrimônio líquido). Os comandos abaixo funcionam como descritos; a fase seguinte é opcional (servidor fechado, autenticação e backup criptografado) (veja o [roadmap](#roadmap)).
>
> **Escopo atual: entrada manual.** Por enquanto **não há importação** de extratos, faturas ou planilhas. Os dados entram pelo painel web ou pela CLI.

## Por que existe

Começou como uma planilha (Google Sheets + Looker Studio) com um bom modelo de dados, mas com limites claros: as visões derivadas eram estáticas e ficavam defasadas, regras sutis (parcelas, fechamento de fatura) viviam em fórmulas frágeis, e o cartão era acompanhado só pelo valor da fatura.

Este projeto troca isso por código testado:

- **Compras uma a uma:** você lança cada compra do cartão (cartão, parcela, fatura), em vez de só o total da fatura.
- **Uma única fonte da verdade:** totais e saldos são calculados na hora a partir dos lançamentos.
- **Regras explícitas e visíveis:** a tela mostra por que uma compra caiu em determinada fatura.
- **Privacidade:** SQLite local; dados reais nunca vão para o git.
- **Evolutivo:** roda no notebook hoje e num servidor fechado (VPN) depois, sem reescrever.

## Funcionalidades

- Contas correntes, cartões de crédito e **contas de investimento**.
- Receitas e despesas; transferências entre contas (inclusive aportes, resgates e pagamento de fatura) ficam fora das receitas e despesas.
- **Compras no cartão** à vista, parceladas ou em andamento ("parcela 3 de 10 na fatura de set/2026"), com cronograma de parcelas.
- **Faturas:** total, status, vencimento, pagamento, conferência com o total do banco e limite comprometido.
- **Parcelamentos:** saldo a pagar exato, sem contar a mesma compra duas vezes, e compromisso mês a mês.
- **Investimentos pensados para o Brasil:** CDB, LCI, LCA, Tesouro, poupança e fundos, com saldo líquido e bruto, vencimentos, liquidez, exposição ao FGC e reserva de emergência.
- **Totais** por mês e ano, conta, cartão e fatura, parcelamentos, investimentos e patrimônio líquido.
- Orçamento por categoria; recorrentes e fixos com alertas (fases seguintes).

## Como funcionam fechamento e parcelas

Cada cartão tem **dia de vencimento**, **quantos dias antes do vencimento a fatura fecha** (de 1 a 27) e **limite**. O fechamento é calculado a partir do vencimento: vence dia 5 e fecha 11 dias antes significa fechar no dia 25 ou 24, conforme o mês.

**Datas congeladas no fechamento.** Enquanto uma fatura ainda não fechou, o fechamento e o vencimento dela acompanham as configurações do cartão: se você mudar o vencimento ou os dias de fechamento, as faturas abertas e as futuras (inclusive as criadas antecipadamente por parcelas) são recalculadas. A partir do dia do fechamento as datas ficam **congeladas**: nada as altera, só você editando as datas daquela fatura. Faturas cujas datas você editou à mão também não são recalculadas, e uma fatura não é alterada se o novo fechamento já tivesse passado.

**Qual fatura recebe a compra.** O dia do fechamento já é o primeiro dia do próximo ciclo: compra **antes** do fechamento entra na fatura que fecha naquele mês; **no dia do fechamento** ou depois, vai para a do mês seguinte (e o melhor dia de compra é o próprio dia do fechamento). A fatura é identificada pelo mês de **fechamento**. Para faturas que já existem vale a data gravada. Exemplo, cartão que vence dia 5 e fecha 11 dias antes do vencimento (o do BB):

| Compra em | Fatura | Fecha | Vence |
|---|---|---|---|
| 20/07/2026 | jul/2026 | 25/07 | 05/08 |
| 25/07/2026 | ago/2026 | 25/08 | 05/09 |
| 23/09/2026 | set/2026 | 24/09 | 05/10 |
| 24/09/2026 | out/2026 | 25/10 | 05/11 |
| 25/12/2026 | jan/2027 | 25/01 | 05/02 |

Setembro tem 30 dias, por isso a fatura de setembro fecha dia 24; em fevereiro, dia 22.

**Parcelas.** Cada parcela vai para a fatura seguinte à anterior: a parcela *k* cai na fatura da parcela 1 mais *k − 1* meses. Informar a fatura de qualquer parcela define todas. Todas as parcelas, inclusive as futuras, são geradas na hora do lançamento.

**Valor.** Informando o total, as parcelas valem o total dividido por N (em centavos) e a **primeira parcela fica com o resto** da divisão. Informando o valor da parcela, as demais assumem o mesmo valor. Qualquer parcela pode ser ajustada. Exemplo: R$ 301,00 em 3x, comprados em 26/07/2026:

| Parcela | Valor | Fatura | Vence |
|---|---|---|---|
| 1/3 | 100,34 | ago/2026 | 05/09 |
| 2/3 | 100,33 | set/2026 | 05/10 |
| 3/3 | 100,33 | out/2026 | 05/11 |

**Compra em andamento.** Informe "parcela atual n de N" e a fatura dela; o sistema gera as parcelas restantes. Exemplo: "3/10 na fatura de set/2026" gera 8 parcelas, de set/2026 a abr/2027.

Antes de salvar, a tela mostra a explicação da fatura e o cronograma completo.

## Investimentos

Cada lugar onde o dinheiro está é uma **conta de investimento** (corretora, banco, caixinha, exchange). Você acompanha no nível da conta (só o saldo) ou, na renda fixa, no nível de cada **aplicação** (um CDB, uma LCI, um título do Tesouro...).

- **Aporte e resgate** são transferências entre a conta corrente e o investimento: não são despesa nem receita.
- **Avaliação:** o saldo **líquido** (valor de resgate hoje, já com IR e IOF estimados) que o app da instituição mostra numa data. O saldo bruto é opcional. Totais, patrimônio e rendimento usam sempre o líquido.
- **O sistema não calcula imposto.** IR regressivo, IOF, come-cotas e isenções (como a de LCI e LCA para pessoa física) mudam por lei; ele registra o que a instituição informa.
- **Rendimento** = valor final − valor inicial − aportes líquidos (rentabilidade simples). Rendimento que só aumenta o saldo investido não conta como receita do mês; proventos pagos na conta corrente (dividendos, JCP, aluguéis de FII, juros) contam.
- **Dados de cada aplicação de renda fixa:** tipo (CDB, LC, LCI, LCA, CRI, CRA, debênture, Tesouro Selic, IPCA+ ou Prefixado, poupança, fundo, previdência...), emissor, indexador e taxa (por exemplo 110% do CDI, IPCA + 6,5%, 12,3% a.a.), data da aplicação, vencimento e liquidez (diária ou no vencimento, com carência).
- **Visões:** escada de vencimentos; liquidez (quanto está disponível hoje, em 30, 90, 180 e 365 dias); **exposição ao FGC** por instituição, com limite configurável; cobertura da **reserva de emergência** em meses de despesas essenciais; posição em 31/12 para ajudar na declaração de IR.

Títulos do Tesouro e prefixados oscilam com o mercado: o valor atual é o de resgate hoje, e o sistema não projeta o valor no vencimento.

Ficam de fora, por enquanto: cálculo de imposto, posições de renda variável (quantidade, preço médio, cotação) e FGTS.

## Telas novas do front v3

- **Painel e Análises:** patrimônio ao longo do tempo (1M, 3M, 1A, Tudo; passe o mouse ou use as setas), ritmo do mês contra a média e o teto do orçamento, fluxo de caixa por mês e rosca por categoria. Todos os números são calculados no servidor.
- **Carta do mês:** resumo do mês fechado escrito por um modelo fixo, com uma nota na margem mostrando a conta de cada número.
- **E se…:** simulador de compra ("geladeira de 4.200 em 10x no BB"): parcelas por fatura, limite, caixa livre e se compensa parcelar. Nada é gravado.
- **Comando ⌘K (Ctrl+K):** navegação, ações rápidas e registrar por frase; o tipo do lançamento é sempre escolha sua.
- O assistente com modelo de linguagem externo ainda **não existe**: nada sai deste computador. O plano e as regras de privacidade estão no CLAUDE.md (seção 16).

## Totais

| Nível | O que mostra |
|---|---|
| Mês e ano | receitas, despesas, saldo, taxa de poupança, aportes líquidos, taxa de investimento, recorrente × variável, por categoria e grupo |
| Conta corrente | saldo, entradas e saídas |
| Cartão | por fatura (total, pago, em aberto, vencimento); geral (faturas a pagar, parcelas futuras, limite comprometido e disponível) |
| Parcelamentos | a pagar por compra, por cartão e geral; cronograma mês a mês |
| Investimentos | por conta e por aplicação (valor, aportes, rendimento), por classe (alocação) e total investido |
| Investimentos (Brasil) | vencimentos, liquidez, exposição ao FGC, cobertura da reserva de emergência, posição em 31/12 |
| Patrimônio | caixa + investimentos − faturas em aberto; parcelas futuras à parte, como compromissos |

Totais que dependem de saldo ou avaliação informados aparecem como **parciais** quando falta alguma conta.

## Como funciona

```
interfaces (CLI, web)  →  application (casos de uso, consultas)  →  domain (regras puras)
                                        ↑
                          infrastructure (SQLite) implementa os ports do domain
```

O domínio não conhece banco nem framework. Fechamento, vencimento e divisão de parcelas são funções puras e testadas. Os casos de uso recebem repositórios por injeção, e só o `container.py` conhece as classes concretas.

## Stack

| Camada | Escolha |
|---|---|
| Linguagem e pacotes | Python 3.12+, uv |
| Persistência | SQLite, SQLAlchemy 2.0, Alembic |
| Web | FastAPI, Jinja2, HTMX, Chart.js (sem build de JS) |
| CLI | Typer |
| Qualidade | pytest, ruff, pyright, import-linter |

## Começando

Pré-requisitos: Python 3.12+ e [uv](https://docs.astral.sh/uv/).

```bash
git clone <url-do-repositorio> financas-pessoais && cd financas-pessoais
uv sync
cp .env.example .env          # ajuste as variáveis, se quiser
uv run financas init          # cria data/, aplica migrations e semeia categorias
uv run financas institution add "Banco do Brasil" --color "#F2B705"
uv run financas account add "Conta corrente" -i "Banco do Brasil"
uv run financas serve         # http://127.0.0.1:8000
```

### Windows + WSL

Se você usa Windows, rode tudo **dentro do WSL** (Ubuntu):

- O repositório fica no disco do Windows (`/mnt/c/Users/mathe/Code/financas-pessoais`). Isso funciona, mas o SQLite nesse disco é mais lento e menos confiável: não rode dois processos escrevendo no banco ao mesmo tempo e **não deixe a pasta `data/` dentro de uma pasta sincronizada na nuvem** (OneDrive, Google Drive), que corrompe o arquivo.
- Use o `git`, o `uv` e o `python` do WSL, não os do Windows. O `.venv` é um ambiente Linux.
- Abra o painel no navegador do Windows em `http://localhost:8000`.
- O Claude Code também roda no WSL. Quando precisar de algo do Windows (abrir uma pasta ou uma URL, por exemplo), ele pode chamar programas do Windows de dentro do WSL, como `explorer.exe .` ou `wslview <url>`.

### Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `FINANCAS_DB_URL` | `sqlite:///data/financas.db` | Banco de dados |
| `FINANCAS_DATA_DIR` | `data/` | Dados e backups |
| `FINANCAS_HOST` | `127.0.0.1` | Endereço do servidor web |
| `FINANCAS_BACKUP_WARN_DAYS` | `7` | Aviso de backup antigo |
| `FINANCAS_VALUATION_STALE_DAYS` | `35` | Aviso de avaliação de investimento desatualizada |
| `FINANCAS_FGC_LIMIT_CENTS` | `25000000` | Limite do FGC por instituição (R$ 250.000) usado na exposição. Confirme o valor vigente no site do FGC |

## Uso

| Comando | O que faz |
|---|---|
| `financas init` | Prepara o ambiente local |
| `financas institution add \| list` | Instituições, com `--color` e `--image` opcionais |
| `financas account add \| list \| deactivate` | Contas correntes e de investimento (cartões: `card add`), com saldo de hoje |
| `financas category add \| list` | Categorias (as iniciais são criadas pelo `init`) |
| `financas add 45,90 "Padaria"` | Receita, despesa ou estorno (`-k income`, `-c food`, `-d 10/07/2026`, `-r`) |
| `financas transfer 500,00 --from "Conta corrente" --to Caixinha` | Transferência entre contas (aporte, resgate, ...) |
| `financas list \| delete` | Lançamentos do mês; apagar um lançamento |
| `financas balance set \| list` | Saldo informado; mostra a diferença para o saldo calculado |
| `financas summary -m 2026-07 \| -y 2026` | Totais do mês ou do ano |
| `financas style institution \| account \| category` | Cor e imagem escolhidas por você |
| `financas log show` | Últimas falhas registradas neste computador (também em Mais → Diagnóstico) |
| `financas card add \| edit \| list` | Cartões: vencimento, dias de fechamento antes do vencimento (`--closes-before-due`), limite, comprometido e disponível |
| `financas card buy` | Compra no cartão: à vista, parcelada ou em andamento; mostra o cronograma e a explicação da fatura antes de salvar |
| `financas card installments` | Parcelas ativas: pagas, restantes e quanto falta pagar |
| `financas statement list \| show \| pay \| inform \| difference \| dates` | Faturas: situação, pagamento (parcial ou total), conferência com o total do banco, diferença e correção de datas |
| `financas invest list` | Contas de investimento: valor atual (líquido), aportes, rendimento, retorno e idade da avaliação |
| `financas invest value CONTA 10.000,00 --gross 10.200,00` | Registra a avaliação de uma conta (líquido e, opcional, bruto) e mostra o rendimento desde a anterior |
| `financas invest flow CONTA 500,00 --from "Conta corrente"` | Aporte (ou `--withdraw` para resgate): é transferência, não receita nem despesa |
| `financas invest settings CONTA --class fixed_income --emergency --tracking holdings` | Classe, reserva de emergência e nível de controle (por conta ou por aplicação) |
| `financas invest holding add \| list \| value \| flow \| redeem \| flags` | Aplicações de renda fixa (CDB, LCI, Tesouro...) numa conta controlada por aplicação |
| `financas invest ladder \| liquidity \| fgc \| emergency` | Escada de vencimentos, faixas de liquidez, exposição ao FGC por grupo e cobertura da reserva de emergência |
| `financas invest year 2026` | Aportes, rendimento capitalizado (não é renda), distribuições e posição em 31/12 |
| `financas budget show [-y 2026] \| set CATEGORIA 700,00 [--clear]` | Matriz de orçamento: categoria × mês, média, meta e média − meta |
| `financas recurring list \| alerts` | Matriz das despesas recorrentes e alertas (sumiu, mudou de valor, apareceu) |
| `financas account flow CONTA -m 2026-07` | Fluxo diário: entradas, saídas, resultado e saldo corrido |
| `financas networth` | Patrimônio líquido: caixa + investimentos − faturas; parcelas futuras à parte |
| `financas backup` | Cópia consistente do banco e das imagens em `data/backups/` |
| `financas serve` | Painel web local |

Exemplos de compra no cartão:

```bash
# R$ 301,00 em 3x, comprados em 26/07; a fatura é calculada e explicada
financas card buy --card "Cartão X" --date 2026-07-26 --total 301,00 --installments 3

# compra já em andamento: parcela 3 de 10, de R$ 61,88, na fatura de set/2026
financas card buy --card "Cartão X" --installment-value 61,88 --current 3 --of 10 --statement 2026-09
```

## Estrutura

```
financas-pessoais/
├── src/financas/
│   ├── domain/            # modelos, regras e ports (sem dependências externas)
│   ├── application/       # casos de uso e consultas dos totais
│   ├── infrastructure/    # SQLAlchemy e migrations
│   ├── interfaces/        # CLI e web (todo o texto em português fica aqui)
│   └── container.py       # composição das dependências
├── tests/                 # unit, contract, integration (dados sintéticos)
└── data/                  # dados reais e backups (ignorado pelo git)
```

O nome do projeto é `financas-pessoais`; o pacote Python e o comando da CLI são `financas`, mais curtos de digitar.

## Desenvolvimento

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports        # confere as regras de camadas
```

**Idioma.** Código, nomes de tabelas e colunas, valores de enum e o `CLAUDE.md` ficam em inglês. Tudo o que o usuário vê (painel e mensagens da CLI) fica em português, com acentos, e só na camada `interfaces/`. Acentos nunca entram em nomes de tabelas, colunas ou enums.

[`CLAUDE.md`](CLAUDE.md) é o contrato do projeto: regras de domínio, arquitetura e convenções. Ele é escrito em inglês para o Claude Code, mas vale para qualquer pessoa que contribua. Se uma regra mudar, atualize-o junto com o código.

## Privacidade e segurança

- Dados reais ficam em `data/`, fora do git. Bancos de dados, planilhas e `.env` estão no `.gitignore`. Os testes usam apenas dados sintéticos.
- **Como os dados são digitados, o banco local é a única cópia.** Faça backup com `financas backup`. Como o projeto está no disco do Windows, uma cópia em `data/backups/` divide o destino desse disco: mantenha também cópias do backup em **outro disco ou dispositivo**. O painel avisa quando o último backup está antigo.
- O servidor web escuta em `127.0.0.1` por padrão.
- Para o futuro servidor fechado: Docker, acesso só por VPN (Tailscale ou WireGuard) e autenticação antes de qualquer exposição.

## Importação única da planilha antiga (2026)

Só os dados de 2026 da planilha antiga entram, uma única vez, e **nada é gravado no banco na etapa de plano**:

```bash
uv sync --extra import                                   # openpyxl, só para isto
uv run financas import plan "docs/Gestão Financeira.xlsx" --year 2026 --holder "seu nome"
```

O comando grava em `data/import/` (ignorado pelo git) cinco arquivos para você revisar: `contas.csv` (contas, cartões com vencimento e dias de fechamento, saldos iniciais), `categorias.csv`, `contrapartes.csv` (decisão para cada pessoa que recebeu ou enviou Pix: própria, terceiro ou categoria), `lancamentos.csv` (cada lançamento planejado, com avisos e as colunas `keep` e `category_override`) e `relatorio.txt` (só contagens e totais). Depois de editar, rode o plano de novo: as decisões são lembradas. A aplicação (`import apply`) só roda com o banco sem lançamentos, faz backup antes e troca o banco só se todas as conferências passarem.

## Roadmap

- [x] **Fase 0, bootstrap:** estrutura do projeto, ferramentas de qualidade, funções de dinheiro e invariantes com testes.
- [x] **Fase 1, núcleo manual:** instituições, contas, categorias, receitas e despesas, transferências, saldos informados, backup, painel mensal.
- [x] **Fase 2, cartões:** compras e parcelas, faturas, pagamento, conferência, limite.
- [x] **Fase 3, investimentos e patrimônio:**
  - [x] **3a, nível da conta:** avaliações (líquido e bruto), aportes e resgates, rendimento, alocação, patrimônio líquido, totais anuais.
  - [x] **3b, aplicações de renda fixa:** dados do contrato, vencimentos, liquidez, exposição ao FGC, reserva de emergência, posição em 31/12.
- [x] **Fase 4, acompanhamento:** orçamento, recorrentes com alertas, fluxo diário.
- [ ] **Fase 5, opcional:** servidor fechado, autenticação e backup criptografado.

**Adiado, só sob pedido:** importação de extratos, faturas e planilhas; classificação automática por regras; Open Finance.

## Licença

Projeto de uso pessoal, sem licença definida.

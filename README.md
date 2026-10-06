# Finanças Pessoais

Gestão financeira pessoal **local, privada e sem build**: contas, cartões com faturas e parcelas, investimentos de renda fixa e patrimônio, calculados na hora a partir dos seus lançamentos. Nenhum dado financeiro sai do seu computador.

> **v1.0.** Fases 0 a 4 prontas (núcleo manual, cartões, investimentos, orçamento e recorrentes), front v3, modo demonstração e importação de lançamentos por arquivo. A fase seguinte é opcional: servidor fechado, autenticação e backup criptografado ([roadmap](#roadmap)).

## Destaques

- **Acabamento de produto.** Sistema de design próprio, com cantos *squircle*, paleta de gama ampla, tema claro, escuro e automático, tipografia editorial (títulos em serifa, números tabulares alinhados) e micro-interações com física de mola: odômetro que rola os dígitos, cartões com inclinação 3D e brilho especular, gráficos que se leem com o mouse ou com as setas.
- **Privacidade local.** SQLite no seu disco, servidor em `127.0.0.1`, sem CDN, sem telemetria, fontes e gráficos embutidos. O **modo privacidade** desfoca todos os valores no navegador (tecla `P`), sem deslocar o layout. Dados reais ficam em `data/`, fora do git.
- **Zero build.** FastAPI + Jinja2 + HTMX; JavaScript puro em módulos estáticos (nada de npm, bundler ou framework). Um `uv sync` e está rodando.
- **Números que não mentem.** Totais e saldos são sempre calculados, nunca guardados; sem dado informado aparece "parcial", "saldo indisponível" ou "limite não informado", nunca zero. Cada fatura e cada parcela vem com a explicação da regra que a colocou ali.
- **Modo demonstração pronto.** `financas demo` abre o painel com uma família fictícia de 2026, sem configurar nada e sem tocar nos dados reais.
- **Importação por arquivo.** A página **Importar dados** (`/importar`) lê um arquivo preparado por você, mostra um resumo do que será registrado, aponta erros por linha e só grava depois da sua confirmação, com backup e tudo-ou-nada.

## Comece em um minuto

Pré-requisitos: Python 3.12+ e [uv](https://docs.astral.sh/uv/).

```bash
git clone <url-do-repositorio> financas-pessoais && cd financas-pessoais
uv sync
```

**Ver a demonstração** (dados fictícios, banco `data/demo.db`, porta 8001):

```bash
uv run financas demo          # abra http://localhost:8001
```

**Usar com os seus dados** (banco `data/financas.db`, porta 8000):

```bash
uv run financas init          # cria data/, aplica as migrations e semeia as categorias
uv run financas serve         # abra http://127.0.0.1:8000
uv run financas backup        # cópia consistente em data/backups/
```

No Windows, rode tudo dentro do WSL (detalhes no [guia de uso](docs/GUIA_DE_USO.md)).

## O que o sistema faz

- Contas correntes, cartões de crédito e contas de investimento; receitas, despesas, estornos e transferências (aportes, resgates e pagamento de fatura ficam fora de receitas e despesas).
- **Cartões:** compras uma a uma, à vista, parceladas ou em andamento, com cronograma; fatura com status, vencimento, pagamento, conferência com o total do banco e limite comprometido; fechamento calculado a partir do vencimento.
- **Investimentos para o Brasil:** CDB, LCI, LCA, Tesouro, poupança e fundos, com saldo líquido e bruto, escada de vencimentos, liquidez, exposição ao FGC, reserva de emergência e posição em 31/12. O sistema não calcula imposto.
- **Painel e Análises:** patrimônio no tempo, ritmo do mês contra a média e o teto, fluxo de caixa, categorias; **Carta do mês** com a conta de cada número na margem; **E se…** para simular uma compra parcelada; **Comando ⌘K / Ctrl+K** para navegar e registrar por frase.
- **Acompanhamento:** orçamento por categoria, recorrentes com alertas, fluxo diário por conta.

## Arquitetura

```
interfaces (CLI, web)  →  application (casos de uso, consultas)  →  domain (regras puras)
                                        ↑
                          infrastructure (SQLite) implementa os ports do domain
```

Domínio sem framework, valores em centavos inteiros, fechamento e parcelas como funções puras com testes em tabela, casos de uso recebendo repositórios por injeção e `container.py` como única raiz de composição. As camadas são verificadas por `import-linter`.

| Camada | Escolha |
|---|---|
| Linguagem e pacotes | Python 3.12+, uv |
| Persistência | SQLite, SQLAlchemy 2.0, Alembic |
| Web | FastAPI, Jinja2, HTMX, módulos JS estáticos (sem build) |
| CLI | Typer |
| Qualidade | pytest, ruff, pyright, import-linter |

Qualidade: mais de 1.300 testes (unidade, contrato, integração e harnesses de navegador para gráficos, privacidade e odômetro).

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run lint-imports
```

## Documentação

| Para quê | Onde |
|---|---|
| Instalar, rodar, demonstração, backup e restauração | [`docs/GUIA_DE_USO.md`](docs/GUIA_DE_USO.md) |
| Como o sistema calcula faturas, parcelas, investimentos e totais | [`docs/CONCEITOS.md`](docs/CONCEITOS.md) |
| Formato do arquivo de importação | [`docs/IMPORTACAO_DADOS.md`](docs/IMPORTACAO_DADOS.md) |
| Registros de engenharia (auditorias, planos, histórico técnico) | `docs-dev/`, mantido só localmente (fora do git) |

[`CLAUDE.md`](CLAUDE.md), o contrato do projeto (regras de domínio, arquitetura e convenções), também fica só localmente.

**Idioma.** Código, tabelas, colunas, enums e especificações em inglês; tudo o que o usuário vê, em português com acentos, apenas na camada `interfaces/`.

## Privacidade e segurança

- Dados reais em `data/`, fora do git: bancos, planilhas, backups e `.env` são ignorados; os testes usam só dados sintéticos. A única exceção versionada é `data/demo.db`, o banco fictício da demonstração.
- Como os dados são digitados, o banco local é a única cópia: faça `financas backup` e guarde cópias em outro disco.
- O servidor escuta só em `127.0.0.1`; nenhum dado financeiro vai para a internet.

## Roadmap

- [x] Fase 0, bootstrap · Fase 1, núcleo manual · Fase 2, cartões · Fase 3, investimentos e patrimônio · Fase 4, orçamento, recorrentes e fluxo diário
- [x] Front v3, modo demonstração, importação por arquivo
- [ ] Fase 5, opcional: servidor fechado (Docker + VPN), autenticação, HTTPS e backup criptografado

**Adiado, só sob pedido:** importação de extratos, faturas e planilhas de banco; classificação automática por regras; Open Finance; assistente com modelo de linguagem externo.

## Licença

Projeto de uso pessoal, sem licença definida.

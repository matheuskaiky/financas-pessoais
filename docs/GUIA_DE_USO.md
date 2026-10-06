# Guia de uso

Como instalar, rodar, fazer backup e alimentar o sistema. Para entender os cálculos, veja [CONCEITOS.md](CONCEITOS.md); para importar muitos lançamentos de uma vez, [IMPORTACAO_DADOS.md](IMPORTACAO_DADOS.md).

## Instalação e primeiro uso

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

- O repositório fica no disco do Windows (`/mnt/c/Users/<seu-usuario>/Code/financas-pessoais`). Isso funciona, mas o SQLite nesse disco é mais lento e menos confiável: não rode dois processos escrevendo no banco ao mesmo tempo e **não deixe a pasta `data/` dentro de uma pasta sincronizada na nuvem** (OneDrive, Google Drive), que corrompe o arquivo.
- Use o `git`, o `uv` e o `python` do WSL, não os do Windows. O `.venv` é um ambiente Linux.
- Abra o painel no navegador do Windows em `http://localhost:8000`.

### Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `FINANCAS_DB_URL` | `sqlite:///data/financas.db` | Banco de dados |
| `FINANCAS_DATA_DIR` | `data/` | Dados e backups |
| `FINANCAS_HOST` | `127.0.0.1` | Endereço do servidor web |
| `FINANCAS_BACKUP_WARN_DAYS` | `7` | Aviso de backup antigo |
| `FINANCAS_VALUATION_STALE_DAYS` | `35` | Aviso de avaliação de investimento desatualizada |
| `FINANCAS_FGC_LIMIT_CENTS` | `25000000` | Limite do FGC por instituição (R$ 250.000) usado na exposição. Confirme o valor vigente no site do FGC |

## Comandos

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
| `financas serve` | Painel web local (seus dados reais, `data/financas.db`) |
| `financas demo` | Painel web com dados **fictícios** (`data/demo.db`, porta 8001), isolado dos dados reais |
| `financas seed-demo --force` | Apaga e recria `data/demo.db` do zero |
| `financas COMANDO --demo` | Qualquer comando de leitura contra o banco de demonstração (`summary --demo`, `networth --demo`...) |

Exemplos de compra no cartão:

```bash
# R$ 301,00 em 3x, comprados em 26/07; a fatura é calculada e explicada
financas card buy --card "Cartão X" --date 2026-07-26 --total 301,00 --installments 3

# compra já em andamento: parcela 3 de 10, de R$ 61,88, na fatura de set/2026
financas card buy --card "Cartão X" --installment-value 61,88 --current 3 --of 10 --statement 2026-09
```

## Modo demonstração

Para mostrar o sistema (portfólio, GitHub, apresentações) sem expor dados reais, o modo demonstração sobe o painel com uma
família **fictícia** de 2026. Ele usa um banco próprio, `data/demo.db`, e nunca abre `data/financas.db`.

### Rodar a demo

```bash
git clone <url-do-repositorio> financas-pessoais && cd financas-pessoais
uv sync
uv run financas demo
```

1. Na primeira execução o comando cria `data/demo.db`, aplica as migrations e gera os dados fictícios (leva alguns
   segundos). Nas seguintes ele só abre o banco que já existe.
2. Abra **http://localhost:8001** no navegador (no Windows com WSL, use o navegador do Windows). Toda tela mostra o aviso
   "Modo demonstração".
3. Para parar, `Ctrl+C` no terminal.

A demo usa a porta **8001**, então pode rodar ao mesmo tempo que o `financas serve` (porta 8000, seus dados reais).
Outra porta: `uv run financas demo --port 8002`.

### Comandos

| Comando | O que faz |
|---|---|
| `uv run financas demo` | Painel web com dados fictícios, em `http://localhost:8001` |
| `uv run financas serve --demo` | Equivale a `financas demo`, mas na porta 8000 (a do `serve`): se o `serve` real estiver rodando, acrescente `--port 8001` |
| `uv run financas seed-demo` | Cria o banco de demonstração se ele ainda não tem dados |
| `uv run financas seed-demo --force` | Apaga `data/demo.db` e gera tudo de novo (use depois de mudar de mês, para as datas acompanharem o dia de hoje) |
| `uv run financas COMANDO --demo` | Qualquer comando contra o banco de demonstração: `summary --demo`, `networth --demo`, `card list --demo`, `statement list --demo`, `invest list --demo`, `backup --demo`... |

Sem `--demo`, os comandos continuam usando seus dados reais. `financas import` não aceita `--demo`.

### O que a demo contém

- **Contas:** Conta BB e Nubank Conta, com saldos informados no fim de cada mês.
- **Cartões:** Ourocard BB (vence dia 5), Nubank Roxinho (vence dia 26) e Inter Black (vence dia 20), cada um com seu
  fechamento. O Nubank passa de 80% do limite (alerta); os outros ficam abaixo.
- **Faturas:** pagas, fechada (a do BB, com diferença para o total do banco), aberta e futuras.
- **Parcelamentos em andamento:** smartphone em 10x, sofá em 6x e passagem aérea em 3x (o resto dos centavos vai para
  a primeira parcela).
- **Investimentos:** Tesouro Selic 2029 e CDB 100% do CDI, com aportes mensais e avaliação a cada fim de mês.
- **Lançamentos:** de janeiro de 2026 até hoje (salário CLT, aluguel, condomínio, supermercado, iFood, Uber, Netflix,
  Spotify, farmácia...), orçamento por categoria e alertas de recorrentes.
- **Gráficos e telas:** patrimônio com mais de 20 pontos, ritmo do mês, fluxo de caixa, categorias, Carta do mês e "E se…".
- **Modo privacidade:** o olho na barra lateral (ou a tecla `P`) desfoca os valores; funciona igual na demo.

### Segurança da demonstração

- A demo guarda tudo em `data/demo.db`, `data/demo_images/`, `data/demo_backups/` e `data/demo_logs/`. Nada vai para a
  pasta dos dados reais (`data/financas.db`, `data/images/`, `data/backups/`).
- O programa recusa usar qualquer outro arquivo de banco no modo demonstração, e `seed-demo --force` só apaga arquivos
  da demo.
- A pasta `data/` está no `.gitignore`, com **uma única exceção: `data/demo.db`**, o banco fictício da demonstração, que vai no repositório para a demo funcionar logo após o clone (os arquivos `-wal`/`-shm` dele e todo o resto de `data/` continuam ignorados). Para renovar as datas depois de mudar de mês, use `financas seed-demo --force`.
- Os dados são inventados. Os nomes de bancos aparecem só como texto e cor; nenhum logotipo é incluído.

## Backup e restauração

- **Fazer backup:** `uv run financas backup` grava uma cópia consistente do banco (API de backup do SQLite) e das imagens em `data/backups/<data>/`. O painel avisa quando o último backup é mais antigo que `FINANCAS_BACKUP_WARN_DAYS` (7 por padrão); o botão "Fazer backup agora" da barra lateral faz o mesmo.
- **Guarde cópias fora deste disco.** `data/backups/` divide o destino do disco do projeto: copie a pasta também para outro disco ou dispositivo (você faz essa cópia; o sistema nunca copia dados reais para fora).
- **Restaurar:** com o servidor parado, copie `financas.db` da pasta do backup desejado para `data/financas.db` (e a pasta `images/` para `data/images/`), e suba de novo com `uv run financas serve`. Antes de sobrescrever, guarde o arquivo atual em outro lugar.
- Atualizações do programa que mudam o banco fazem um backup automático antes de migrar. A importação por arquivo também faz backup antes de gravar.

## Privacidade e segurança

- Dados reais ficam em `data/`, fora do git. Bancos de dados, planilhas e `.env` estão no `.gitignore`. Os testes usam apenas dados sintéticos.
- **Como os dados são digitados, o banco local é a única cópia.** Faça backup com `financas backup`. Como o projeto está no disco do Windows, uma cópia em `data/backups/` divide o destino desse disco: mantenha também cópias do backup em **outro disco ou dispositivo**. O painel avisa quando o último backup está antigo.
- O servidor web escuta em `127.0.0.1` por padrão.
- Para o futuro servidor fechado: Docker, acesso só por VPN (Tailscale ou WireGuard) e autenticação antes de qualquer exposição.

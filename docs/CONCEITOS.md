# Conceitos: como o sistema calcula

Resumo das regras de negócio que o painel mostra. A especificação completa fica em `CLAUDE.md` (seção 9) no repositório do desenvolvedor.

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

## Transferências ligadas

Uma transferência entre contas suas são **duas pontas** (saída e entrada) que dividem o mesmo identificador (`transfer_id`). Elas nascem juntas, na mesma operação do banco de dados, com o mesmo valor em módulo e sinais opostos, e **nunca** podem estar na mesma conta. Editar qualquer ponta (valor, data, contas, descrição, nota de investimento) atualiza a outra na mesma operação; apagar uma ponta apaga a outra. Se um dos lados é uma conta que o sistema não controla, existe uma ponta só. Transferências não são receita nem despesa; já o **saldo** da conta corrente as inclui, como o extrato.

Lançamentos antigos que são as duas metades da mesma transferência (digitados como receita e despesa) podem ser ligados com `financas reconcile-transfers`: ele compara valor, sinais opostos, contas diferentes, data (até 1 dia) e palavras como PIX/TED, mostra a confiança de cada par e só liga os seguros com `--apply` (backup antes, tudo ou nada). Ao ligar, os dois deixam de contar como receita e despesa.

## Categorias neutras (reembolsos e terceiros)

Algum dinheiro só **atravessa** a sua conta: você paga a conta de luz do seu pai e ele devolve depois, ou adianta uma compra de um amigo. Contar isso como despesa e como receita distorce o custo de vida, a taxa de poupança e o orçamento. Uma categoria marcada como **neutra** resolve: os lançamentos continuam movendo o saldo do banco **exatamente** (a conciliação com o extrato não muda um centavo), mas ficam **fora** de gastos, receitas, orçamento, recorrentes, gráficos, ranking de estabelecimentos e “Gastos do dia”. O resumo informa à parte o que ficou **em trânsito** (pago e recebido). Uma categoria neutra aceita despesas e receitas, para o par ida e volta ficar na mesma categoria.

## Investimentos: notas, caixa livre e marcação a mercado

Há dois jeitos de acompanhar uma conta de investimento, e ela usa só um por vez:

- **Controle Global (por conta):** um saldo único da conta, informado por data. Serve para caixinhas e fundos que você não quer detalhar.
- **Controle por Notas/Aplicações:** cada CDB, LCI ou título é uma **nota** com seu próprio histórico de saldos, taxa, vencimento e liquidez.

Numa conta por notas, o dinheiro tem três lugares, todos calculados (nunca guardados):

- **Aporte líquido** = transferências recebidas − transferências enviadas.
- **Saldo livre em caixa** = aporte líquido − custo das notas (valor aplicado mais os aportes diretos nelas). É o dinheiro que chegou e ainda espera uma nota. Uma nota cadastrada sem aporte registrado mostra o aviso “aplicados sem aporte registrado”.
- **Saldo aplicado** = valor de mercado das notas ativas, pelo último saldo informado mais os aportes depois dele.
- **Patrimônio da conta** = saldo livre + saldo aplicado; **lucro** = patrimônio − aporte líquido. Resgatar uma nota não mexe no saldo livre, e o ganho realizado continua no lucro.

A **marcação a mercado por snapshots** é isso: cada vez que você informa o saldo de uma nota numa data, nasce um ponto na linha do tempo; o sistema nunca projeta valores futuros. Em cada ponto: **lucro** = saldo − custo, **rentabilidade** = lucro ÷ custo, **rendimento do período** = variação do saldo − aportes e resgates do período. Trocar de controle nunca é silencioso: com notas ativas o sistema explica e, se você confirmar, arquiva as notas (ficam no histórico, fora dos totais); transferências nunca impedem a troca.

## Gastos do dia e Saldo da conta

No cabeçalho de cada dia da lista de lançamentos:

- **Gastos do dia** = despesas − estornos daquele dia, **sem** transferências e **sem** categorias neutras. É uma medida de consumo.
- **Saldo da conta** = o saldo acumulado no **fim** do dia: o último saldo informado ± todas as movimentações (receitas, despesas e as duas pontas das transferências) até aquele dia, como no extrato. Sem filtro, soma as contas correntes; filtrando por uma conta, é o saldo dela. Sem saldo informado, “indisponível”.

Por isso um dia pode ter “Gastos do dia: R$ 0,00” e o saldo mudar (uma transferência ou um reembolso mexem no saldo, mas não são gasto).

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

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

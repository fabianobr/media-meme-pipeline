# Avaliação de conceitos-semente congelados

## Implementação

O pipeline aceita `--concepts-file` com uma lista de posts e 1-5 candidatas completas por
post. Nesse modo:

- a geração inicial e o escritor de humor são ignorados;
- o crítico independente executa uma rodada;
- limites de tamanho, escalada, virada e elementos inventados continuam ativos;
- a rubrica mantém os mesmos mínimos;
- a candidata escolhida é persistida mesmo quando rejeitada, para auditoria;
- renderização continua bloqueada enquanto `humor_approved` for falso.

Entradas do experimento:

- `docs/experiments/frozen-cat-gerald-post.json`;
- `docs/experiments/frozen-gerald-concept-seeds.json`.

## Resultado

Cinco sementes foram avaliadas. O crítico escolheu:

- setup: `A CASA TEM UM GATO`;
- escalada: `MAS GERALD TEM NOME E POSTURA`;
- punchline: `O IMÓVEL É DELE`.

Notas: `source_fit=9`, `natural_ptbr=8`, `surprise=7`, `laugh=6`,
`visual_payoff=8`. A candidata foi rejeitada por não atingir surpresa e humor 8/10.
Nenhuma renderização foi iniciada.

Artefatos: `data/media-pipeline/e2e-gerald-seeded-v2/2026-07-05/`.

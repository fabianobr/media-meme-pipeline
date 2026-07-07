# Gate de qualidade: bilhete do garçom

## Escopo

- Post congelado: `t3_1uhan3t`.
- Entrada local: `docs/experiments/frozen-waiter-doctor-post.json`.
- Nenhum acesso ao Reddit ao vivo.
- Nenhuma renderização autorizada sem aprovação de humor e qualidade.

## Correções aplicadas

- Chamadas estruturadas ao Qwen usam `think: false`; antes disso o Ollama podia devolver
  `message.content` vazio e manter a resposta no canal de raciocínio.
- Respostas do escritor aceitam array JSON direto ou o envelope explícito
  `{"candidates": [...]}`; outros formatos continuam inválidos.
- Cada chamada registra modelo, rodada, timeout, início, fim, duração, estado, tamanho e
  prévia limitada da resposta.
- Escritor e crítico agora podem usar modelos Ollama distintos por meio de
  `--humor-model` e `--humor-critic-model`.
- A visão resume semanticamente documentos e não deve transformar pessoas ou ações
  mencionadas no texto em elementos visíveis.

## Resultado

Execução válida com escritor `qwen3:8b` e crítico independente `llama3:latest`:

- três rodadas concluídas dentro dos limites;
- nenhuma candidata aprovada;
- melhor avaliação: `source_fit=8`, `natural_ptbr=6`, `surprise=7`, `laugh=5`,
  `visual_payoff=4`;
- resultado persistido como `rejected`;
- ComfyUI não foi acionado e nenhum vídeo foi produzido.

Artefatos: `data/media-pipeline/e2e-waiter-grounded/2026-07-05/`.

## Aprendizado

O gate agora diferencia três falhas que antes pareciam uma só: resposta vazia causada
pelo modo de raciocínio, modelo que ignora o schema e conteúdo válido porém fraco. O post
do bilhete é semanticamente claro, mas exige leitura de documento e teve baixo potencial
visual; não deve ser usado como primeiro E2E audiovisual aprovado.

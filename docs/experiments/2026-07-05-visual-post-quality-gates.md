# Gates de qualidade em posts predominantemente visuais

## Candidatos

Foram avaliados três posts congelados, sempre sem renderização antes da aprovação:

1. Kelsey Plum arremessando uma camisa para a arquibancada;
2. pessoa em scooter urbana usando capacete de motocross;
3. gato laranja chamado Gerald.

## Resultados

- **Kelsey Plum:** rejeitado porque a miniatura disponível não mostrava a ação descrita
  no título; a visão detectou uma pessoa segurando uma lata. A fonte visual não sustentava
  um I2V fiel ao post.
- **Scooter:** rejeitado porque as candidatas apenas descreviam o capacete e a scooter;
  uma rodada posterior também retornou JSON inválido.
- **Gerald com `qwen3:8b`:** melhor rodada chegou a `source_fit=9`, `surprise=9`,
  `laugh=7`; abaixo do mínimo obrigatório de humor.
- **Gerald com `gemma4:31b`:** três rodadas levaram aproximadamente oito minutos. A
  melhor candidata foi "GERALD ESTÁ ANALISANDO SEU CURRÍCULO", com `source_fit=9`,
  `natural_ptbr=8`, `surprise=7`, `laugh=6`, `visual_payoff=8`. Também rejeitada.

Nenhum candidato acionou o ComfyUI e nenhum vídeo foi produzido.

## Conclusão

O gate técnico está funcionando: fontes visuais inconsistentes, JSON inválido, descrição
disfarçada de piada e humor abaixo do mínimo não avançam. Trocar um modelo de 8B por um
de 31B aumentou custo e coerência, mas não atingiu o limiar de humor. O próximo experimento
deve separar a geração da ideia da avaliação: aceitar até cinco conceitos-semente
congelados, mantendo crítico independente, rubrica determinística e zero aprovação por
fallback.

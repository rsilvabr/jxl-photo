# Bugs e Melhorias Pendentes

## 🐛 Bugs Confirmados

### 1. `--embed-thumbnail` sendo passado para JPEG→JXL (transcoder)
**Status:** ✅ CORRIGIDO - Movido para só TIFF→JXL encoder

**Problema:** O wrapper passava `--embed-thumbnail` para `jxl_jpeg_transcoder.py`, mas esse script não aceita esse parâmetro.
**Erro:** `error: unrecognized arguments: --embed-thumbnail`

### 2. Subcomando ausente no transcoder (transcode/convert)
**Status:** ✅ CORRIGIDO

**Problema:** O `jxl_jpeg_transcoder.py` exige subcomando explícito (`transcode` ou `convert`), mas o wrapper não estava passando.
**Erro:** `ERROR: Directory input requires explicit 'transcode' or 'convert' subcommand`

**Solução aplicada:** Adicionar `transcode` ou `convert` ao cmd baseado no `conversion_type`:
- `transcode_lossless` → `transcode`
- `convert_lossy` → `convert`

### 3. Pergunta "Convert to sRGB?" aparece no TRANSCODE lossless
**Status:** ✅ CORRIGIDO

**Problema:** No wizard Step 6, pergunta "Convert to sRGB?" sempre que é JXL→JPEG/PNG, mesmo quando é **TRANSCODE LOSSLESS**.

**Isso é incorreto porque:**
- Transcode lossless = preserva pixels exatos, sem conversão de cor
- Converter para sRGB = altera valores dos pixels (não é lossless!)
- São operações incompatíveis

**Onde:** `jxl_photo.py` linha 1776
```python
if origin == 'jxl' and dest in ['jpeg', 'png'] and status.get('magick'):
    convert_icc = Confirm.ask("Convert to sRGB? ...")  # ❌ Sempre pergunta!
```

**Correção:** Só perguntar quando NÃO for transcode lossless:
```python
if origin == 'jxl' and dest in ['jpeg', 'png'] and status.get('magick'):
    if workflow.get('conversion_type') != 'transcode_lossless':  # ✅ Adicionar check
        convert_icc = Confirm.ask("Convert to sRGB? ...")
```

---

## ⚠️ Limitações / Melhorias Pendentes

### 2. JXL → TIFF: Preview JPEG é sempre criado (não configurável)
**Arquivo:** `jxl_tiff_decoder.py`
**Status:** ✅ CORRIGIDO v1.5

**Problema:** O decoder sempre adicionava um preview JPEG dentro do TIFF (hardcoded `True`). Não havia opção para desligar.

**Solução implementada:**
- Adicionada flag `--no-preview` no `jxl_tiff_decoder.py`
- Atualizado wizard em `jxl_photo.py` para perguntar "Add JPEG preview?" no Step 6
- Preview é mostrado no sumário (Step 7)
- Default: criar preview (para manter compatibilidade)

---

### 3. JPEG → JXL: Não há opção de embed thumbnail
**Arquivo:** `jxl_jpeg_transcoder.py`

**Problema:** O transcoder de JPEG para JXL não implementa a criação de thumbnail embutido no JXL.

**Nota:** No momento só `jxl_tiff_encoder.py` (TIFF→JXL) suporta `--embed-thumbnail`.

**Solução proposta:**
- Implementar embed thumbnail no `jxl_jpeg_transcoder.py` para JPEG→JXL
- Ou esconder a opção no wizard quando for JPEG→JXL (já corrigido parcialmente)

**Impacto:** Usuários que convertem JPEG→JXL não podem ter thumbnail preview no JXL.

---

### 4. Inconsistência: TIFF→JXL thumbnail é opcional, JXL→TIFF é obrigatório
**Problema:** Comportamento inconsistente entre as direções.

| Direção | Thumbnail | Configurável? |
|---------|-----------|---------------|
| TIFF → JXL | Opcional | ✅ Sim (via `--embed-thumbnail`) |
| JXL → TIFF | Sempre | ❌ Não (hardcoded `True`) |
| JPEG → JXL | Não suportado | N/A |

**Solução ideal:**
- Padronizar: todos os formatos de saída (JXL, TIFF) deveriam ter thumbnail opcional
- Adicionar `--embed-thumbnail` para todos os encoders
- Adicionar `--no-preview` para todos os decoders que criam preview

---

## 📝 Notas Técnicas

### Implementação atual:

**TIFF → JXL (`jxl_tiff_encoder.py`):**
```python
EMBED_JPEG_THUMBNAIL = False  # Padrão
# CLI: --embed-thumbnail para ativar
```

**JXL → TIFF (`jxl_tiff_decoder.py`):**
```python
ADD_JPEG_PREVIEW = True  # Hardcoded, sempre ativo
JPEG_PREVIEW_SIZE = 256  # Hardcoded
# Sem opção CLI para desativar
```

**JPEG → JXL (`jxl_jpeg_transcoder.py`):**
```python
# Não implementado
# Não aceita --embed-thumbnail
```

---

## 🎯 Prioridade

### 🔴 ALTÍSSIMA — Bug #35
**JXL → JPEG AUTO mode não funciona para pastas**  
**Status:** ✅ CORRIGIDO v1.5

**Problema:** O transcoder exigia `--force-transcode` ou `--force-convert` para diretórios. O auto-detect só funcionava para arquivos individuais.

**Solução implementada:**
- Adicionada função `cmd_auto()` no `jxl_jpeg_transcoder.py` que:
  1. Lista todos os arquivos JXL no diretório
  2. Verifica jbrd em cada arquivo
  3. Separa em duas listas: com jbrd (transcode lossless) e sem jbrd (convert lossy)
  4. Processa cada grupo com o método apropriado
- Atualizado `jxl_photo.py` para ativar a opção "JPEG Auto-Detect" no Step 2

1. **Alta:** Implementar `--no-preview` em JXL→TIFF (usuário solicitou)
2. **Média:** Implementar `--embed-thumbnail` em JPEG→JXL (paridade de features)
3. **Baixa:** Padronizar nomenclatura (embed vs preview)

---

*Atualizado em: 2026-04-11*

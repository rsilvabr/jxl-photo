# jxl-photo Testbench

Testbench automatizado para o toolkit de conversão JXL. Executa testes em todas as direções de conversão e verifica integridade.

## 📋 Requisitos

- Python 3.8+
- Pasta de testes com imagens de exemplo (ver estrutura abaixo)
- Todas as dependências do toolkit instaladas (cjxl, djxl, exiftool, etc.)

## 📁 Estrutura de Pasta de Testes

```
E:\TESTAR (ou pasta configurada)
├── tiff\          # Arquivos TIFF 16-bit para teste
│   ├── foto1.tif
│   └── foto2.tif
├── jxl\           # Arquivos JXL para teste
│   ├── foto1.jxl
│   └── foto2.jxl
└── jpg\           # Arquivos JPEG para teste
    ├── foto1.jpg
    └── foto2.jpg
```

> **Dica:** Use uma pasta com poucos arquivos (3-5 de cada tipo) para testes rápidos.

## 🚀 Uso

### Executar todos os testes
```powershell
python testbench.py
```

### Modo rápido (apenas 3 primeiros testes)
```powershell
python testbench.py --quick
```

### Manter arquivos de saída para inspeção
```powershell
python testbench.py --keep-outputs
```

### Ver saída detalhada
```powershell
python testbench.py --verbose
```

### Usar pasta de testes diferente
```powershell
python testbench.py --input-dir "D:\MeusTestes" --output-dir "D:\TestResults"
```

## 🧪 Testes Realizados

| # | Teste | Script | Verificação |
|---|-------|--------|-------------|
| 1 | TIFF → JXL | `jxl_tiff_encoder.py` | Arquivos JXL criados, compressão verificada |
| 2 | JXL → TIFF | `jxl_tiff_decoder.py` | Arquivos TIFF criados, preview gerado |
| 3 | JPEG → JXL | `jxl_jpeg_transcoder.py` | Arquivos JXL criados |
| 4 | JXL → JPEG | `jxl_jpeg_transcoder.py` | Arquivos JPEG criados |
| 5 | Roundtrip | encoder + decoder | TIFF → JXL → TIFF, verificação de integridade |

## 📊 Interpretação de Resultados

### ✓ PASS
Teste completou com sucesso e todos os arquivos esperados foram criados.

### ✗ FAIL
Teste falhou. Possíveis causas:
- Script com erro de sintaxe
- Dependência faltando (cjxl, djxl, exiftool)
- Bug no código
- Arquivos de entrada corrompidos

### ⊘ SKIP
Teste foi pulado (arquivos de entrada não encontrados na pasta de testes).

## 🔧 Solução de Problemas

### "No TIFF files found"
Certifique-se de que a pasta `tiff/` existe dentro do diretório de testes e contém arquivos `.tif` ou `.tiff`.

### "Encoder failed"
Verifique se:
- `cjxl.exe` está no PATH
- `exiftool.exe` está no PATH
- Pacotes Python estão instalados: `pip install tifffile numpy pillow`

### "Command timed out"
Arquivos muito grandes ou muitos arquivos podem causar timeout. Use o modo `--quick` ou reduza o número de arquivos de teste.

## 📝 Exemplo de Saída

```
============================================================
jxl-photo Testbench v1.3
============================================================

Started: 2026-04-10 18:30:00
Input dir: E:\TESTAR
Output dir: E:\TESTAR_OUTPUT

============================================================
TEST 1: TIFF → JXL (jxl_tiff_encoder.py)
============================================================

ℹ Found 6 TIFF files to convert
✓ Converted 6 TIFF files to JXL

============================================================
TEST 2: JXL → TIFF (jxl_tiff_decoder.py)
============================================================

ℹ Found 6 JXL files to convert
⚠ JPEG preview generation had issues (check logs)
✓ Converted 6 JXL files to TIFF

============================================================
TEST SUMMARY
============================================================

✓ PASS  TIFF → JXL          Created 6 JXL files
✓ PASS  JXL → TIFF          Created 6 TIFF files
✓ PASS  JPEG → JXL          Created 4 JXL files
✓ PASS  JXL → JPEG          Created 6 JPEG files
✓ PASS  Roundtrip           Roundtrip successful: DSC00001.tif

Results:
  Passed:  5
  Failed:  0
  Skipped: 0
  Total:   5

Finished: 2026-04-10 18:35:00
```

## 🔄 Integração com Workflow de Desenvolvimento

### Antes de commitar mudanças
```powershell
# 1. Rodar testbench
python testbench.py

# 2. Se tudo passar, commitar

# 3. Se falhar, corrigir antes de commitar
```

### Testes de regressão
```powershell
# Após corrigir bug, rodar testbench para garantir que não quebrou nada
python testbench.py --verbose
```

## 🐛 Reportando Bugs

Se o testbench encontrar falhas:

1. Execute com `--verbose` para obter detalhes
2. Execute com `--keep-outputs` para inspecionar arquivos
3. Verifique os logs em `Logs/` dentro da pasta do script
4. Reporte o problema com:
   - Saída do testbench
   - Arquivos de log relevantes
   - Descrição do ambiente (Windows versão, Python versão)

## 📄 Licença

MIT License - mesmo do projeto principal.

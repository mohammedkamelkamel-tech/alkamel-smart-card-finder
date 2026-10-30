from pathlib import Path
p = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
s = p.read_text()

# Use the system document picker for image selection, which returns a stable document URI.
s = s.replace(
'''    private val pickImage = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) readImage(uri)
    }''',
'''    private val pickImage = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            try { contentResolver.takePersistableUriPermission(uri, android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION) } catch (_: Exception) {}
            readImage(uri)
        } else showStatus("لم يتم اختيار صورة. حاول مرة أخرى.")
    }'''
)
s = s.replace('pickImage.launch("image/*")', 'pickImage.launch(arrayOf("image/*"))')
s = s.replace('import android.graphics.BitmapFactory', 'import android.graphics.BitmapFactory\nimport android.graphics.Canvas\nimport android.graphics.Color\nimport android.graphics.Paint\nimport android.graphics.ColorMatrix\nimport android.graphics.ColorMatrixColorFilter')

# Add a visible progress bar immediately below the import buttons.
s = s.replace('    private lateinit var category: Spinner', '    private lateinit var category: Spinner\n    private lateinit var importProgress: ProgressBar')
s = s.replace(
'''        root.addView(actionButton("اختيار صورة من الهاتف") { pickImage.launch(arrayOf("image/*")) })''',
'''        root.addView(actionButton("اختيار صورة من الهاتف") { pickImage.launch(arrayOf("image/*")) })
        importProgress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100; progress = 0; visibility = View.GONE
        }
        root.addView(importProgress)'''
)

# Replace image OCR path with original + contrast-enhanced + thresholded attempts.
a = s.index('    private fun readImage(uri: Uri) {')
b = s.index('    private suspend fun recognize(image: InputImage): Text =', a)
s = s[:a] + '''    private fun readImage(uri: Uri) {
        lifecycleScope.launch {
            showStatus("تم اختيار الصورة — جارٍ تحسينها وقراءة الرقم...")
            try {
                val bitmap = withContext(Dispatchers.IO) {
                    contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
                        ?: throw IllegalStateException("تعذر فتح الصورة المختارة")
                }
                val text = recognizeBest(bitmap)
                bitmap.recycle()
                val candidates = Regex("\\\\d{7,15}").findAll(normalizeDigits(text)).map { it.value }.distinct().toList()
                showOcrCandidates(candidates, text)
            } catch (e: Exception) {
                showStatus("تعذرت قراءة الصورة: ${e.localizedMessage}")
            }
        }
    }

    private suspend fun recognizeBest(bitmap: Bitmap): String {
        val attempts = mutableListOf<String>()
        attempts.add(recognize(InputImage.fromBitmap(bitmap, 0)).text)
        for (threshold in listOf(false, true)) {
            val processed = enhanceForDarkDigits(bitmap, threshold)
            try { attempts.add(recognize(InputImage.fromBitmap(processed, 0)).text) }
            finally { processed.recycle() }
        }
        return attempts.maxByOrNull { Regex("\\\\d{7,15}").findAll(normalizeDigits(it)).count() }.orEmpty()
    }

    private fun enhanceForDarkDigits(source: Bitmap, threshold: Boolean): Bitmap {
        val scale = minOf(2.0f, 2200f / maxOf(source.width, source.height).toFloat()).coerceAtLeast(1f)
        val w = (source.width * scale).toInt().coerceAtLeast(1)
        val h = (source.height * scale).toInt().coerceAtLeast(1)
        val output = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
        paint.colorFilter = ColorMatrixColorFilter(ColorMatrix().apply { setSaturation(0f) })
        Canvas(output).drawBitmap(source, null, android.graphics.Rect(0, 0, w, h), paint)
        val pixels = IntArray(w * h)
        output.getPixels(pixels, 0, w, 0, 0, w, h)
        for (i in pixels.indices) {
            val c = pixels[i]
            val gray = (Color.red(c) * 0.299 + Color.green(c) * 0.587 + Color.blue(c) * 0.114).toInt()
            val value = if (threshold) { if (gray < 175) 0 else 255 } else ((gray - 128) * 1.8 + 128).toInt().coerceIn(0, 255)
            pixels[i] = Color.rgb(value, value, value)
        }
        output.setPixels(pixels, 0, w, 0, 0, w, h)
        return output
    }

''' + s[b:]

# Import progress and final confirmation including source filename.
a = s.index('    private fun importDocument(uri: Uri) {')
b = s.index('    private fun queryName(uri: Uri): String {', a)
s = s[:a] + '''    private fun importDocument(uri: Uri) {
        val cat = categories[category.selectedItemPosition]
        lifecycleScope.launch {
            importProgress.progress = 0
            importProgress.visibility = View.VISIBLE
            showStatus("جارٍ استيراد الملف...")
            val name = queryName(uri)
            try {
                val added = withContext(Dispatchers.IO) {
                    when {
                        name.lowercase(Locale.ROOT).endsWith(".pdf") || contentResolver.getType(uri) == "application/pdf" -> readPdfNumbers(uri, cat, name)
                        else -> readTextNumbers(uri, cat, name)
                    }
                }
                importProgress.progress = 100
                showStatus("تمت إضافة الملف $name — عدد الكروت الجديدة: $added")
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("تمت إضافة الملف")
                    .setMessage("اسم الملف: $name\\nالفئة: $cat\\nعدد الكروت الجديدة: $added")
                    .setPositiveButton("حسناً", null).show()
                refreshStats()
            } catch (e: Exception) {
                showStatus("تعذر استيراد الملف $name: ${e.localizedMessage}")
            } finally { importProgress.visibility = View.GONE }
        }
    }

''' + s[b:]

# TXT progress updates in batches so large files visibly advance from 1 to 100.
a = s.index('    private fun readTextNumbers(uri: Uri, cat: String, name: String): Int {')
b = s.index('    private suspend fun readPdfNumbers(uri: Uri, cat: String, name: String): Int {', a)
s = s[:a] + '''    private fun readTextNumbers(uri: Uri, cat: String, name: String): Int {
        val text = contentResolver.openInputStream(uri)?.bufferedReader()?.use { it.readText() }
            ?: throw IllegalStateException("تعذر قراءة الملف")
        val normalized = normalizeDigits(text)
        val matches = Regex("(?<!\\\\d)\\\\d{7,15}(?!\\\\d)").findAll(normalized).map { it.value }.toList()
        val batches = matches.chunked(500)
        var added = 0
        batches.forEachIndexed { index, batch ->
            added += db.insertMany(batch, cat, name, 0)
            val percent = (((index + 1) * 100f) / batches.size.coerceAtLeast(1)).toInt()
            runOnUiThread { importProgress.progress = percent.coerceIn(1, 100); showStatus("إضافة كروت الملف $name: $percent%") }
        }
        if (matches.isEmpty()) runOnUiThread { importProgress.progress = 100 }
        return added
    }

''' + s[b:]

# Improve PDF progress and use enhanced OCR for dark printed numbers.
s = s.replace('val scale = minOf(2.0, 1800.0 / maxOf(page.width, page.height))', 'val scale = minOf(2.5, 2200.0 / maxOf(page.width, page.height))')
s = s.replace('val text = recognize(InputImage.fromBitmap(bitmap, 0)).text', 'val text = recognizeBest(bitmap)')
s = s.replace('showStatus("قراءة PDF: الصفحة ${i + 1} من ${renderer.pageCount}")', 'importProgress.progress = (((i + 1) * 100f) / renderer.pageCount.coerceAtLeast(1)).toInt(); showStatus("قراءة الملف $name: ${importProgress.progress}% — الصفحة ${i + 1} من ${renderer.pageCount}")')

# Visual redesign: coordinated red, purple, green, gold, cyan and blue palette.
s = s.replace('setBackgroundColor(0xFFF4F7F5.toInt())', 'setBackgroundColor(0xFFF5F6FA.toInt())')
s = s.replace('setTextColor(0xFF126B3A.toInt()); setPadding(4, 12, 4, 18)',
              'setTextColor(0xFF283B72.toInt()); setPadding(12, 22, 12, 22); textSize = 23f; typeface = android.graphics.Typeface.DEFAULT_BOLD')
s = s.replace('setTextColor(0xFF52645A.toInt())', 'setTextColor(0xFF596579.toInt())')
s = s.replace('setTextColor(0xFF263B30.toInt())', 'setTextColor(0xFF34415F.toInt()); typeface = android.graphics.Typeface.DEFAULT_BOLD')
s = s.replace('setTextColor(0xFF24543A.toInt())', 'setTextColor(0xFF2864D7.toInt())')
s = s.replace('setTextColor(0xFF7A5B22.toInt())', 'setTextColor(0xFF9A6B12.toInt())')
# Give the six main actions distinct but coordinated colors and rounded corners.
s = s.replace('''        isAllCaps = false
        setOnClickListener { action() }''', '''        isAllCaps = false
        textSize = 15f
        typeface = android.graphics.Typeface.DEFAULT_BOLD
        setTextColor(android.graphics.Color.WHITE)
        val palette = listOf(0xFFE53935.toInt(), 0xFF8E44AD.toInt(), 0xFF20A464.toInt(),
            0xFFD5A126.toInt(), 0xFF27B6DE.toInt(), 0xFF2864D7.toInt())
        val color = when {
            text.contains("استيراد") -> palette[3]
            text.contains("تصوير") -> palette[0]
            text.contains("اختيار صورة") -> palette[1]
            text.contains("ابحث") -> palette[5]
            text.contains("إحصائيات") -> palette[2]
            else -> palette[(text.hashCode().ushr(1) % palette.size)]
        }
        background = android.graphics.drawable.GradientDrawable().apply {
            setColor(color)
            cornerRadius = 18f
        }
        setPadding(14, 12, 14, 12)
        val params = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT)
        params.setMargins(0, 7, 0, 7)
        layoutParams = params
        setOnClickListener { action() }''')
# Add a subtle white panel behind the search field and a colorful progress indicator.
s = s.replace('''            setPadding(12, 10, 12, 10)
        }
        root.addView(searchBox)''', '''            setPadding(12, 10, 12, 10)
            background = android.graphics.drawable.GradientDrawable().apply {
                setColor(0xFFFFFFFF.toInt())
                setStroke(2, 0xFFD9E0EE.toInt())
                cornerRadius = 16f
            }
        }
        root.addView(searchBox)''')
s = s.replace('''            max = 100; progress = 0; visibility = View.GONE
        }''', '''            max = 100; progress = 0; visibility = View.GONE
            progressTintList = android.content.res.ColorStateList.valueOf(0xFF27B6DE.toInt())
        }''')

p.write_text(s)
print("Applied OCR, image-picker, and import-progress updates.")

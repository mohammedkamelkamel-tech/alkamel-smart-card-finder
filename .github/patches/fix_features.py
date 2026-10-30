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
        val matches = Regex("(?<!\\\\d)\\\\d{11}(?!\\\\d)").findAll(normalized).map { it.value }.distinct().toList()
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


# Native dark navy/gold dashboard, aligned with the Google AI Studio preview.
a = s.index('    private fun buildScreen() {')
b = s.index('    private fun label(text: String)', a)
new_screen = r'''    private fun buildScreen() {
        val navy = 0xFF070C20.toInt()
        val panel = 0xFF111A2E.toInt()
        val gold = 0xFFFFB300.toInt()
        val muted = 0xFF9AA8C0.toInt()
        val cyan = 0xFF20C7E8.toInt()
        val green = 0xFF13B981.toInt()
        val purple = 0xFF9B5DE5.toInt()
        val blue = 0xFF4385F5.toInt()
        fun bg(color: Int, stroke: Int = 0xFF273650.toInt(), radius: Float = 22f) =
            android.graphics.drawable.GradientDrawable().apply { setColor(color); setStroke(1, stroke); cornerRadius = radius }
        fun txt(value: String, size: Float, color: Int, bold: Boolean = false) =
            TextView(this).apply {
                text = value; textSize = size; setTextColor(color); gravity = Gravity.RIGHT
                if (bold) typeface = android.graphics.Typeface.DEFAULT_BOLD
                setPadding(2, 5, 2, 5)
            }
        fun lp() = LinearLayout.LayoutParams(-1, -2).apply { setMargins(0, 7, 0, 7) }
        fun panelBox(color: Int = panel) = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(16, 14, 16, 14); background = bg(color); layoutParams = lp()
        }
        fun action(caption: String, color: Int, callback: () -> Unit) = Button(this).apply {
            text = caption; isAllCaps = false; textSize = 14f; setTextColor(0xFFFFFFFF.toInt())
            typeface = android.graphics.Typeface.DEFAULT_BOLD; background = bg(color, color, 18f)
            setPadding(12, 8, 12, 8); layoutParams = lp(); setOnClickListener { callback() }
        }

        val outer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; layoutDirection = View.LAYOUT_DIRECTION_RTL; setBackgroundColor(navy)
        }
        val scroll = ScrollView(this)
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(16, 14, 16, 20); layoutDirection = View.LAYOUT_DIRECTION_RTL
        }
        scroll.addView(content)
        outer.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(8, 10, 8, 10); background = bg(0xFF10192D.toInt(), 0xFF263650.toInt(), 0f)
        }
        header.addView(TextView(this).apply {
            text = "▣"; textSize = 30f; gravity = Gravity.CENTER; setTextColor(navy)
            background = bg(0xFFFF9800.toInt(), 0xFFFF9800.toInt(), 18f)
            layoutParams = LinearLayout.LayoutParams(58, 58).apply { setMargins(4, 0, 10, 0) }
        })
        val brand = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        brand.addView(txt("الكامل", 22f, 0xFFFFFFFF.toInt(), true))
        brand.addView(txt("الباحث الذكي", 13f, gold, true))
        brand.addView(txt("com.alkamel.cardfinder", 11f, muted))
        header.addView(brand, LinearLayout.LayoutParams(0, -2, 1f))
        header.addView(TextView(this).apply {
            text = "🛡 " + db.count(); textSize = 16f; gravity = Gravity.CENTER
            setTextColor(gold); background = bg(0xFF20283A.toInt()); setPadding(12, 12, 12, 12)
        })
        content.addView(header, lp())

        val hero = panelBox(0xFF202B3E.toInt())
        hero.addView(txt("✧  محرك بحث ذكي للأرقام المفقودة والتالفة", 14f, gold, true).apply {
            gravity = Gravity.CENTER; background = bg(0xFF302A27.toInt(), 0xFF72521B.toInt(), 28f); setPadding(10, 9, 10, 9)
        }, lp())
        hero.addView(txt("الكامل – الباحث الذكي عن الكروت", 27f, 0xFFFFFFFF.toInt(), true))
        hero.addView(txt("استعادة أرقام كروت الشحن من الصور والملفات حتى لو فُقد جزء منها أو كانت مشوهة بسبب الكشط أو رداءة التصوير.", 16f, 0xFFCAD3E2.toInt()))
        val stats = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            background = bg(0xFF090F22.toInt()); setPadding(14, 12, 14, 12)
        }
        val stat = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        stat.addView(txt("إجمالي الكروت المخزنة", 13f, muted))
        stat.addView(txt(db.count().toString() + " كرت", 27f, 0xFFFFFFFF.toInt(), true))
        stats.addView(stat, LinearLayout.LayoutParams(0, -2, 1f))
        stats.addView(TextView(this).apply {
            text = "♢"; textSize = 30f; gravity = Gravity.CENTER; setTextColor(gold)
            background = bg(0xFF282522.toInt(), 0xFF73521A.toInt()); layoutParams = LinearLayout.LayoutParams(62, 62)
        })
        hero.addView(stats, lp())
        hero.addView(txt("البحث عن رقم الكرت", 18f, 0xFFFFFFFF.toInt(), true))
        val searchRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            background = bg(0xFF080E21.toInt(), 0xFF35445E.toInt()); setPadding(8, 5, 8, 5)
        }
        searchBox = EditText(this).apply {
            hint = "أدخل الرقم أو الأرقام الظاهرة"; textSize = 17f; setSingleLine(true)
            setTextColor(0xFFFFFFFF.toInt()); setHintTextColor(0xFF71809A.toInt())
            layoutDirection = View.LAYOUT_DIRECTION_LTR; gravity = Gravity.CENTER
            setPadding(7, 8, 7, 8); background = null
        }
        searchRow.addView(searchBox, LinearLayout.LayoutParams(0, -2, 1f))
        searchRow.addView(action("بحث  ←", 0xFFFF9800.toInt()) { searchCards() })
        hero.addView(searchRow, lp())
        hero.addView(txt("أمثلة سريعة: 366365467 (تسلسل جزئي)   •   984*120 (بداية ونهاية)", 12f, muted))
        content.addView(hero, lp())

        content.addView(txt("فئات الكروت المعتمدة", 21f, 0xFFFFFFFF.toInt(), true))
        val grid = android.widget.GridLayout(this).apply { columnCount = 2; layoutDirection = View.LAYOUT_DIRECTION_RTL }
        val colors = listOf(green, blue, gold, purple)
        categories.forEachIndexed { index, cat ->
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL; setPadding(13, 13, 13, 13)
                background = bg(0xFF101A2E.toInt(), colors[index])
            }
            card.addView(txt(cat, 17f, colors[index], true))
            card.addView(txt(db.countCategory(cat).toString() + " كرت مخزن", 14f, 0xFFD4DCEC.toInt()))
            val gp = android.widget.GridLayout.LayoutParams().apply {
                width = 0; height = -2; columnSpec = android.widget.GridLayout.spec(index % 2, 1f)
                rowSpec = android.widget.GridLayout.spec(index / 2); setMargins(5, 5, 5, 5)
            }
            grid.addView(card, gp)
        }
        content.addView(grid, lp())

        content.addView(txt("الوظائف والخدمات الرئيسية", 21f, 0xFFFFFFFF.toInt(), true))
        val services = listOf(
            Triple("البحث عن كرت", "البحث بالرقم الكامل أو البداية أو النهاية أو تسلسل مفقود", gold),
            Triple("التصوير والبحث (OCR)", "التقاط صورة الكرت بالكاميرا أو اختيار صورة والتعرف البصري دون إنترنت", cyan),
            Triple("استيراد ملفات TXT و PDF", "استيراد ملفات متعددة وتحديد الفئة واستخراج أرقام الكروت", green),
            Triple("قاعدة بيانات الكروت", "استعراض وإدارة الكروت المخزنة وتفاصيل مصدرها", blue)
        )
        services.forEachIndexed { index, item ->
            val service = panelBox(0xFF10192C.toInt())
            service.addView(txt(item.first, 18f, 0xFFFFFFFF.toInt(), true))
            service.addView(txt(item.second, 14f, muted))
            service.setOnClickListener {
                when (index) {
                    0 -> searchBox.requestFocus()
                    1 -> openCamera()
                    2 -> openFile.launch(arrayOf("text/plain", "application/pdf", "application/octet-stream"))
                    else -> showStatus("إجمالي الكروت: " + db.count())
                }
            }
            content.addView(service)
        }

        content.addView(txt("استيراد سريع", 20f, 0xFFFFFFFF.toInt(), true))
        category = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, categories)
        }
        content.addView(category, lp())
        content.addView(action("استيراد ملف TXT أو PDF", green) {
            openFile.launch(arrayOf("text/plain", "application/pdf", "application/octet-stream"))
        })
        content.addView(action("تصوير الكرت بالكاميرا", cyan) { openCamera() })
        content.addView(action("اختيار صورة من الهاتف", purple) { pickImage.launch(arrayOf("image/*")) })
        importProgress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100; progress = 0; visibility = View.GONE
            progressTintList = android.content.res.ColorStateList.valueOf(cyan)
        }
        content.addView(importProgress, lp())
        status = txt("جاهز — إجمالي الكروت: " + db.count(), 14f, cyan).apply { gravity = Gravity.CENTER }
        content.addView(status, lp())
        content.addView(txt("النتائج / الأرقام المستخرجة", 20f, 0xFFFFFFFF.toInt(), true))
        results = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        content.addView(results, lp())
        content.addView(action("تحديث إحصائيات المخزون", gold) { refreshStats() })
        content.addView(txt("قراءة الصور آلية وقد تخطئ في الأرقام المتشابهة. راجع النتائج وأكد الرقم الصحيح قبل استخدامه.", 12f, muted))

        val bottom = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
            setPadding(4, 8, 4, 8); background = bg(0xFF10192D.toInt(), 0xFF273650.toInt(), 0f)
        }
        listOf("الرئيسية", "البحث الذكي", "كاميرا OCR", "الاستيراد", "القاعدة", "المزيد").forEachIndexed { i, name ->
            val color = when (i) { 2 -> cyan; 0 -> gold; else -> muted }
            val item = TextView(this).apply {
                text = when (i) { 0 -> "⌂\\n" + name; 1 -> "⌕\\n" + name; 2 -> "◎\\n" + name; 3 -> "⇧\\n" + name; 4 -> "▤\\n" + name; else -> "☷\\n" + name }
                textSize = 11f; gravity = Gravity.CENTER; setTextColor(color); setPadding(2, 5, 2, 5)
            }
            item.setOnClickListener {
                when (i) {
                    0 -> scroll.smoothScrollTo(0, 0)
                    1 -> { scroll.smoothScrollTo(0, hero.top); searchBox.requestFocus() }
                    2 -> openCamera()
                    3 -> openFile.launch(arrayOf("text/plain", "application/pdf", "application/octet-stream"))
                    4 -> showStatus("إجمالي الكروت: " + db.count())
                    else -> refreshStats()
                }
            }
            bottom.addView(item, LinearLayout.LayoutParams(0, -2, 1f))
        }
        outer.addView(bottom, LinearLayout.LayoutParams(-1, -2))
        setContentView(outer)
    }

'''
s = s[:a] + new_screen + s[b:]
needle = '    fun count(): Int {'
if 'fun countCategory(category: String)' not in s:
    s = s.replace(needle, '''    fun countCategory(category: String): Int {
        readableDatabase.rawQuery("SELECT COUNT(*) FROM cards WHERE category=?", arrayOf(category)).use {
            return if (it.moveToFirst()) it.getInt(0) else 0
        }
    }

''' + needle)
p.write_text(s)
print("Applied native dark preview-matching dashboard.")

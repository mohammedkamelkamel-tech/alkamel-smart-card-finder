from pathlib import Path
import re
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


# Correct card inventory accounting and partial-number search.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
# Remove the count helper inserted earlier so the database block can be replaced atomically.
k = re.sub(r"\n    fun countCategory\(category: String\): Int \{.*?\n    \}\n", "\n", k, flags=re.S)
new_db = '''    fun insertMany(numbers: List<String>, category: String, source: String, page: Int): Int {
        val db = writableDatabase
        var added = 0
        db.beginTransaction()
        try {
            // A card number is globally unique, even when repeated on another PDF page/file.
            numbers.asSequence().map { it.trim() }.distinct().forEach { number ->
                if (number.length == 11 && number.all { it in '0'..'9' }) {
                    val exists = db.rawQuery("SELECT 1 FROM cards WHERE number=? LIMIT 1", arrayOf(number)).use { it.moveToFirst() }
                    if (!exists) {
                        val values = ContentValues().apply {
                            put("number", number); put("category", category)
                            put("source", source); put("page", page)
                        }
                        if (db.insertWithOnConflict("cards", null, values, android.database.sqlite.SQLiteDatabase.CONFLICT_IGNORE) != -1L) added++
                    }
                }
            }
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
        return added
    }

    fun search(query: String): List<CardRecord> {
        val q = query.filter { it in '0'..'9' }
        if (q.length < 3) return emptyList()
        val pattern = "%" + q.toCharArray().joinToString("%") + "%"
        val cursor = readableDatabase.rawQuery(
            "SELECT number, MIN(category), MIN(source), MIN(page) FROM cards WHERE number LIKE ? GROUP BY number LIMIT 2000",
            arrayOf(pattern)
        )
        val out = mutableListOf<CardRecord>()
        cursor.use { while (it.moveToNext()) out.add(CardRecord(it.getString(0), it.getString(1), it.getString(2), it.getInt(3))) }
        fun skipped(number: String): Int {
            var pos = 0
            for (digit in q) {
                val at = number.indexOf(digit, pos)
                if (at < 0) return Int.MAX_VALUE
                pos = at + 1
            }
            return number.length - q.length
        }
        return out.sortedWith(compareBy<CardRecord> { if (it.number == q) 0 else 1 }
            .thenBy { if (it.number.contains(q)) 0 else 1 }
            .thenBy { skipped(it.number) }
            .thenBy { it.number }).take(200)
    }

    fun countCategory(category: String): Int {
        readableDatabase.rawQuery("SELECT COUNT(DISTINCT number) FROM cards WHERE category=?", arrayOf(category)).use {
            return if (it.moveToFirst()) it.getInt(0) else 0
        }
    }

    fun count(): Int {
        readableDatabase.rawQuery("SELECT COUNT(DISTINCT number) FROM cards", null).use {
            return if (it.moveToFirst()) it.getInt(0) else 0
        }
    }
'''
# Replace the CardDb methods from insertMany through the end of CardDb.
db_start = k.index("    fun insertMany(")
db_end = k.rfind("\n}")
if db_start < 0 or db_end < db_start:
    raise SystemExit("CardDb section not found; refusing to patch.")
k = k[:db_start] + new_db + k[db_end:]
main.write_text(k)
print("Applied unique-card counting and ordered partial-number search.")
k = k.replace("SELECT COUNT(*) FROM cards WHERE category=?", "SELECT COUNT(DISTINCT number) FROM cards WHERE category=?")
main.write_text(k)
print("Applied unique-card counting and ordered partial-number search.")


# Make the search workflow explicit and validate the selected denomination against the file name.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()

a = k.index("    private fun searchCards() {")
b = k.index("    private fun displayMatches(", a)
k = k[:a] + '''    private fun searchCards() {
        val raw = normalizeDigits(searchBox.text.toString())
        val q = raw.filter { it in '0'..'9' }
        results.removeAllViews()
        if (q.length < 3) {
            showStatus("اكتب رقم الكرت أو 3 أرقام على الأقل في مربع «أدخل الرقم أو الأرقام الظاهرة» ثم اضغط بحث.")
            searchBox.requestFocus()
            return
        }
        val matches = db.search(q)
        displayMatches(matches, q)
        if (matches.isNotEmpty()) {
            var parent: android.view.ViewParent? = results.parent
            while (parent != null && parent !is ScrollView) parent = parent.parent
            val scroll = parent as? ScrollView
            scroll?.post { scroll.smoothScrollTo(0, results.top) }
        }
    }

''' + k[b:]

k = k.replace(
'''        root.addView(actionButton("ابحث في جميع الكروت") { searchCards() })''',
'''        searchBox.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH ||
                actionId == android.view.inputmethod.EditorInfo.IME_ACTION_DONE) {
                searchCards(); true
            } else false
        }
        root.addView(actionButton("بحث عن رقم الكرت  🔎") { searchCards() })'''
)

a = k.index("    private fun importDocument(uri: Uri) {")
b = k.index("    private fun queryName(uri: Uri): String {", a)
k = k[:a] + '''    private fun importDocument(uri: Uri) {
        val selectedCategory = categories.getOrNull(category.selectedItemPosition)
            ?: run { showStatus("حدد فئة الكروت أولاً."); return }
        val name = queryName(uri)
        val digitsInName = normalizeDigits(name)
        val detected = Regex("(?<!\\\\d)(100|200|250|500)(?!\\\\d)")
            .find(digitsInName)?.groupValues?.getOrNull(1)
        val selectedDigits = selectedCategory.filter { it in '0'..'9' }
        if (detected != null && detected != selectedDigits) {
            AlertDialog.Builder(this)
                .setTitle("الفئة لا تطابق الملف")
                .setMessage("اسم الملف: $name\\\\nالفئة المكتشفة من اسم الملف: $detected ريال\\\\nالفئة التي اخترتها: $selectedCategory\\\\nتم إلغاء الاستيراد حتى لا تُحفظ الكروت في فئة خاطئة. اختر الفئة الصحيحة أو الملف الصحيح.")
                .setPositiveButton("حسناً", null).show()
            showStatus("تم رفض الملف: فئته لا تطابق الفئة المحددة.")
            return
        }
        val proceed = {
            lifecycleScope.launch {
                showStatus("جارٍ استيراد $name إلى فئة $selectedCategory...")
                try {
                    val added = withContext(Dispatchers.IO) {
                        when {
                            name.lowercase(Locale.ROOT).endsWith(".pdf") ||
                                contentResolver.getType(uri) == "application/pdf" -> readPdfNumbers(uri, selectedCategory, name)
                            else -> readTextNumbers(uri, selectedCategory, name)
                        }
                    }
                    showStatus("اكتمل الاستيراد: أضيف $added كرت جديد إلى فئة $selectedCategory")
                    AlertDialog.Builder(this@MainActivity)
                        .setTitle("اكتمل الاستيراد")
                        .setMessage("الملف: $name\\\\nالفئة: $selectedCategory\\\\nالكروت الجديدة: $added")
                        .setPositiveButton("حسناً", null).show()
                    refreshStats()
                } catch (e: Exception) {
                    showStatus("تعذر استيراد الملف $name: \${e.localizedMessage}")
                }
            }
        }
        if (detected == null) {
            AlertDialog.Builder(this)
                .setTitle("تأكيد فئة الملف")
                .setMessage("لم أتمكن من معرفة الفئة من اسم الملف. هل تريد استيراد «$name» ضمن الفئة المحددة: $selectedCategory؟")
                .setNegativeButton("إلغاء", null)
                .setPositiveButton("تأكيد الاستيراد") { _, _ -> proceed() }
                .show()
        } else proceed()
    }

''' + k[b:]
main.write_text(k)
print("Patched search feedback, keyboard search, and denomination/file validation.")


# OCR recovery: use multiple OCR passes, combine visible digit fragments in order,
# search the local card database, and explicitly display the missing middle digits.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
a = k.index("    private fun readImage(uri: Uri) {")
b = k.index("    private suspend fun recognize(image: InputImage): Text =", a)
k = k[:a] + '''    private fun readImage(uri: Uri) {
        lifecycleScope.launch {
            showStatus("جارٍ قراءة الأرقام الظاهرة من الصورة والبحث في ملفات الكروت...")
            results.removeAllViews()
            try {
                val bitmap = withContext(Dispatchers.IO) {
                    contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
                        ?: throw IllegalStateException("تعذر فتح الصورة المختارة")
                }
                val attempts = recognizeAttempts(bitmap)
                bitmap.recycle()
                val fragments = attempts.flatMap { text ->
                    normalizeDigits(text).split(Regex("[^0-9]+")).filter { it.length >= 3 }
                }.distinct().sortedByDescending { it.length }

                data class Recovery(val record: CardRecord, val visible: String, val missing: String)
                val recovered = linkedMapOf<String, Recovery>()

                fragments.filter { it.length >= 7 }.forEach { fragment ->
                    db.search(fragment).forEach { record ->
                        recovered.putIfAbsent(record.number, Recovery(record, fragment, ""))
                    }
                }

                for (i in fragments.indices) {
                    for (j in fragments.indices) {
                        if (i == j) continue
                        val first = fragments[i]
                        val second = fragments[j]
                        if (first.length < 3 || second.length < 3 || first.length + second.length > 11) continue
                        db.search(first + second).forEach { record ->
                            val firstAt = record.number.indexOf(first)
                            val secondAt = if (firstAt >= 0) record.number.indexOf(second, firstAt + first.length) else -1
                            if (firstAt >= 0 && secondAt >= firstAt + first.length) {
                                val missing = record.number.substring(firstAt + first.length, secondAt)
                                recovered.putIfAbsent(record.number, Recovery(record, first + second, missing))
                            }
                        }
                    }
                }

                if (recovered.isEmpty()) {
                    showStatus("لم أجد رقماً مطابقاً في الملفات. جرّب تصوير الكرت بشكل أوضح مع ظهور سطر الرقم.")
                    results.addView(TextView(this@MainActivity).apply {
                        text = "الأرقام التي قرأتها الكاميرا: " + fragments.joinToString("  |  ").ifBlank { "لم يتم التعرف على أرقام" }
                        textSize = 15f; setTextColor(0xFFFFFFFF.toInt()); setPadding(12, 12, 12, 12)
                    })
                } else {
                    showStatus("تم العثور على " + recovered.size + " احتمال مطابق في قاعدة البيانات.")
                    recovered.values.take(30).forEachIndexed { index, item ->
                        val card = LinearLayout(this@MainActivity).apply {
                            orientation = LinearLayout.VERTICAL
                            setPadding(14, 12, 14, 12)
                            background = android.graphics.drawable.GradientDrawable().apply {
                                setColor(0xFF111A2E.toInt()); setStroke(2, 0xFF20B981.toInt()); cornerRadius = 18f
                            }
                        }
                        card.addView(TextView(this@MainActivity).apply {
                            text = "الاحتمال \${index + 1}"; textSize = 13f; setTextColor(0xFFFFB300.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "الأرقام المقروءة: " + item.visible; textSize = 16f; setTextColor(0xFFCAD3E2.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "الأرقام التي أكملها التطبيق: " + if (item.missing.isBlank()) "لا يوجد — الرقم مقروء بالكامل" else item.missing
                            textSize = 17f; setTextColor(0xFF20C7E8.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "رقم الكرت بالكامل: " + item.record.number; textSize = 22f
                            typeface = android.graphics.Typeface.DEFAULT_BOLD; setTextColor(0xFFFFFFFF.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "الفئة: " + item.record.category; textSize = 14f; setTextColor(0xFF9AA8C0.toInt())
                        })
                        results.addView(card)
                        (card.layoutParams as? LinearLayout.LayoutParams)?.setMargins(0, 8, 0, 8)
                    }
                }
            } catch (e: Exception) {
                showStatus("تعذرت قراءة الصورة والبحث عنها: \${e.localizedMessage}")
            }
        }
    }

    private suspend fun recognizeAttempts(bitmap: Bitmap): List<String> {
        val attempts = mutableListOf<String>()
        attempts.add(recognize(InputImage.fromBitmap(bitmap, 0)).text)
        for (threshold in listOf(false, true)) {
            val processed = enhanceForDarkDigits(bitmap, threshold)
            try { attempts.add(recognize(InputImage.fromBitmap(processed, 0)).text) }
            finally { processed.recycle() }
        }
        return attempts
    }

''' + k[b:]
# Keep the existing recognizeBest helper; OCR image flow uses recognizeAttempts.
main.write_text(k)
print("Added OCR fragment recovery and explicit missing-digit/full-number results.")


# Search incomplete card numbers within the selected denomination only.
# Matching is order-preserving: missing digits may be at the beginning, middle, or end.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()

a = k.index("    fun search(query: String): List<CardRecord> {")
b = k.index("    fun countCategory(category: String): Int {", a)
k = k[:a] + '''    fun search(query: String, category: String? = null): List<CardRecord> {
        val q = query.filter { it in '0'..'9' }
        if (q.length < 3) return emptyList()
        val pattern = "%" + q.toCharArray().joinToString("%") + "%"
        val sql = if (category.isNullOrBlank()) {
            "SELECT number, MIN(category), MIN(source), MIN(page) FROM cards WHERE number LIKE ? GROUP BY number LIMIT 5000"
        } else {
            "SELECT number, MIN(category), MIN(source), MIN(page) FROM cards WHERE number LIKE ? AND category=? GROUP BY number LIMIT 5000"
        }
        val args = if (category.isNullOrBlank()) arrayOf(pattern) else arrayOf(pattern, category)
        val cursor = readableDatabase.rawQuery(sql, args)
        val out = mutableListOf<CardRecord>()
        cursor.use { while (it.moveToNext()) out.add(CardRecord(it.getString(0), it.getString(1), it.getString(2), it.getInt(3))) }

        fun isSubsequence(candidate: String, needle: String): Boolean {
            var pos = 0
            for (digit in needle) {
                val at = candidate.indexOf(digit, pos)
                if (at < 0) return false
                pos = at + 1
            }
            return true
        }
        fun score(number: String): Int {
            if (number == q) return -1000
            if (number.startsWith(q)) return 0
            if (number.endsWith(q)) return 1
            if (number.contains(q)) return 2
            return 10 + (number.length - q.length)
        }
        return out.filter { isSubsequence(it.number, q) }
            .sortedWith(compareBy<CardRecord> { score(it.number) }
                .thenBy { it.number.length - q.length }
                .thenBy { it.number })
            .take(200)
    }

''' + k[b:]

# Typed search and OCR recovery are both constrained to the currently selected card category.
k = k.replace("val matches = db.search(q)", "val matches = db.search(q, categories.getOrNull(category.selectedItemPosition))")
k = k.replace("db.search(fragment).forEach", "db.search(fragment, categories.getOrNull(category.selectedItemPosition)).forEach")
k = k.replace("db.search(first + second).forEach", "db.search(first + second, categories.getOrNull(category.selectedItemPosition)).forEach")
k = k.replace('showStatus("تم العثور على " + recovered.size + " احتمال مطابق في قاعدة البيانات.")',
              'showStatus("تم العثور على " + recovered.size + " احتمال مطابق في فئة " + categories.getOrNull(category.selectedItemPosition) + ".")')
main.write_text(k)
print("Updated database matching to search within the selected category and support missing digits at either end or in the middle.")


# Restore image preprocessing helpers required by the camera and OCR flows.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
marker = "    private suspend fun recognize(image: InputImage): Text ="
if "private fun enhanceForDarkDigits(source: Bitmap, threshold: Boolean): Bitmap" not in k:
    helpers = '''    private suspend fun recognizeBest(bitmap: Bitmap): String =
        recognizeAttempts(bitmap).maxByOrNull {
            Regex("\\\\\\\\d{7,15}").findAll(normalizeDigits(it)).count()
        }.orEmpty()

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
            val value = if (threshold) {
                if (gray < 175) 0 else 255
            } else {
                ((gray - 128) * 1.8 + 128).toInt().coerceIn(0, 255)
            }
            pixels[i] = Color.rgb(value, value, value)
        }
        output.setPixels(pixels, 0, w, 0, 0, w, h)
        return output
    }

'''
    if marker not in k:
        raise SystemExit("Could not find OCR recognizer insertion point")
    k = k.replace(marker, helpers + marker, 1)
main.write_text(k)
print("Restored recognizeBest and image enhancement helpers.")


# Make the manual search field and denomination selectors unmistakably visible and interactive.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()

# Separate search-category selection from import-category selection, while synchronizing both.
if "private lateinit var searchCategory: Spinner" not in k:
    k = k.replace("private lateinit var category: Spinner", "private lateinit var category: Spinner\n    private lateinit var searchCategory: Spinner")

# Add a clear category selector directly above the manual search input.
needle = '''        hero.addView(txt("البحث عن رقم الكرت", 18f, 0xFFFFFFFF.toInt(), true))
        val searchRow = LinearLayout(this).apply {'''
replacement = '''        hero.addView(txt("1) اختر فئة الكرت التي تريد البحث فيها", 17f, gold, true))
        searchCategory = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_item, categories).also {
                it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            }
            setBackground(bg(0xFF101A2E.toInt(), gold, 12f))
            setPadding(14, 8, 14, 8)
            minimumHeight = 54
            setSelection(0)
        }
        hero.addView(searchCategory, lp())
        hero.addView(txt("2) اكتب الرقم الناقص أو الجزء الظاهر من الكرت", 17f, 0xFFFFFFFF.toInt(), true))
        val searchRow = LinearLayout(this).apply {'''
if needle not in k:
    raise SystemExit("Search area anchor not found")
k = k.replace(needle, replacement, 1)

# Give the EditText a visible outlined background and fixed touch height.
k = k.replace(
'''            hint = "أدخل الرقم أو الأرقام الظاهرة"; textSize = 17f; setSingleLine(true)
            setTextColor(0xFFFFFFFF.toInt()); setHintTextColor(0xFF71809A.toInt())
            layoutDirection = View.LAYOUT_DIRECTION_LTR; gravity = Gravity.CENTER
            setPadding(7, 8, 7, 8); background = null''',
'''            hint = "اكتب رقم الكرت الناقص هنا"; textSize = 19f; setSingleLine(true)
            setTextColor(0xFFFFFFFF.toInt()); setHintTextColor(0xFFB7C4D8.toInt())
            layoutDirection = View.LAYOUT_DIRECTION_LTR; gravity = Gravity.CENTER
            setPadding(10, 8, 10, 8); background = bg(0xFF17243A.toInt(), 0xFF5D7395.toInt(), 10f)
            minHeight = 58
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
            imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH'''
)
k = k.replace('searchRow.addView(searchBox, LinearLayout.LayoutParams(0, -2, 1f))',
              'searchRow.addView(searchBox, LinearLayout.LayoutParams(0, 60, 1f))')
k = k.replace('searchRow.addView(action("بحث  ←", 0xFFFF9800.toInt()) { searchCards() })',
              'searchRow.addView(action("بحث 🔎", 0xFFFF9800.toInt()) { searchCards() }, LinearLayout.LayoutParams(-2, 60))')

# Make the denomination cards themselves selectable and show selection with a check mark/border.
old = '''            card.addView(txt(cat, 17f, colors[index], true))
            card.addView(txt(db.countCategory(cat).toString() + " كرت مخزن", 14f, 0xFFD4DCEC.toInt()))
            val gp = android.widget.GridLayout.LayoutParams().apply {'''
new = '''            val title = txt(cat + if (index == 0) "  ✓" else "", 17f, colors[index], true)
            card.addView(title)
            card.addView(txt(db.countCategory(cat).toString() + " كرت مخزن", 14f, 0xFFD4DCEC.toInt()))
            card.isClickable = true
            card.isFocusable = true
            card.setOnClickListener {
                searchCategory.setSelection(index)
                category.setSelection(index)
                for (childIndex in 0 until grid.childCount) {
                    val child = grid.getChildAt(childIndex) as? LinearLayout ?: continue
                    child.background = bg(0xFF101A2E.toInt(), if (childIndex == index) colors[childIndex] else 0xFF35445E.toInt(), 12f)
                    val heading = child.getChildAt(0) as? TextView
                    heading?.text = categories[childIndex] + if (childIndex == index) "  ✓ محددة" else ""
                }
                showStatus("تم تحديد فئة $cat للبحث والاستيراد.")
            }
            val gp = android.widget.GridLayout.LayoutParams().apply {'''
if old not in k:
    raise SystemExit("Category grid anchor not found")
k = k.replace(old, new, 1)

# Re-style import spinner so its selected category is visible against the dark screen.
old = '''        category = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, categories)
        }
        content.addView(category, lp())'''
new = '''        content.addView(txt("حدد فئة الملف قبل اختياره", 16f, gold, true), lp())
        category = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_item, categories).also {
                it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            }
            setBackground(bg(0xFF101A2E.toInt(), gold, 12f))
            setPadding(14, 8, 14, 8)
            minimumHeight = 56
            setSelection(searchCategory.selectedItemPosition)
            onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
                override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                    if (searchCategory.selectedItemPosition != position) searchCategory.setSelection(position)
                    showStatus("الفئة المحددة للاستيراد والبحث: \${categories[position]}")
                }
            }
        }
        searchCategory.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (::category.isInitialized && category.selectedItemPosition != position) category.setSelection(position)
            }
        }
        content.addView(category, lp())'''
if old not in k:
    raise SystemExit("Import spinner anchor not found")
k = k.replace(old, new, 1)

# Always scope manual search and OCR fragment recovery to the clearly selected search category.
k = k.replace("categories.getOrNull(category.selectedItemPosition)", "categories.getOrNull(searchCategory.selectedItemPosition)")
k = k.replace("categories.getOrNull(category.selectedItemPosition)", "categories.getOrNull(searchCategory.selectedItemPosition)")
k = k.replace("val cat = categories[category.selectedItemPosition]", "val cat = categories[category.selectedItemPosition]")

# Ensure keyboard search executes the same selected-category search.
if "searchBox.setOnEditorActionListener" not in k:
    k = k.replace(
        'searchRow.addView(searchBox, LinearLayout.LayoutParams(0, 60, 1f))',
        '''searchBox.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH ||
                actionId == android.view.inputmethod.EditorInfo.IME_ACTION_DONE) {
                searchCards(); true
            } else false
        }
        searchRow.addView(searchBox, LinearLayout.LayoutParams(0, 60, 1f))'''
    )

main.write_text(k)
print("Added visible manual search input, clear category pickers, selectable category cards, and synchronized search/import category.")


# Final search/import correction: dot masks represent unknown single digit positions.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()

# Preserve dots in the user query instead of stripping them as non-digits.
k = k.replace(
'''        val raw = normalizeDigits(searchBox.text.toString())
        val q = raw.filter { it in '0'..'9' }''',
'''        val raw = normalizeDigits(searchBox.text.toString())
        val q = raw.filter { it in '0'..'9' || it == '.' }'''
)
k = k.replace(
'''        if (q.length < 3) {
            showStatus("اكتب رقم الكرت أو 3 أرقام على الأقل في مربع «أدخل الرقم أو الأرقام الظاهرة» ثم اضغط بحث.")''',
'''        if (q.count { it in '0'..'9' } < 3) {
            showStatus("اكتب أرقام الكرت، واستبدل كل خانة غير واضحة بنقطة (.) ثم اضغط بحث.")'''
)

# Replace the DB matcher with positional wildcard matching for 11-digit masks.
a = k.index("    fun search(query: String, category: String? = null): List<CardRecord> {")
b = k.index("    fun countCategory(category: String): Int {", a)
search_impl = '''    fun search(query: String, category: String? = null): List<CardRecord> {
        val mask = query.filter { it in '0'..'9' || it == '.' }
        val digitCount = mask.count { it in '0'..'9' }
        if (digitCount < 3) return emptyList()
        val sql = if (category.isNullOrBlank()) {
            "SELECT number, MIN(category), MIN(source), MIN(page) FROM cards GROUP BY number LIMIT 20000"
        } else {
            "SELECT number, MIN(category), MIN(source), MIN(page) FROM cards WHERE category=? GROUP BY number LIMIT 20000"
        }
        val args = if (category.isNullOrBlank()) emptyArray() else arrayOf(category)
        val cursor = readableDatabase.rawQuery(sql, args)
        val all = mutableListOf<CardRecord>()
        cursor.use { while (it.moveToNext()) all.add(CardRecord(it.getString(0), it.getString(1), it.getString(2), it.getInt(3))) }

        fun positionalMatch(number: String, pattern: String): Boolean {
            if (number.length != pattern.length) return false
            return pattern.indices.all { i -> pattern[i] == '.' || pattern[i] == number[i] }
        }
        fun subsequence(number: String, needle: String): Boolean {
            var at = 0
            for (digit in needle.filter { it in '0'..'9' }) {
                at = number.indexOf(digit, at)
                if (at < 0) return false
                at++
            }
            return true
        }

        val hasDots = mask.contains('.')
        val matches = if (hasDots) {
            all.filter { positionalMatch(it.number, mask) || positionalMatch(it.number, mask.reversed()) }
        } else {
            val needle = mask
            all.filter { subsequence(it.number, needle) }
                .sortedWith(compareBy<CardRecord> {
                    when {
                        it.number == needle -> 0
                        it.number.startsWith(needle) -> 1
                        it.number.endsWith(needle) -> 2
                        it.number.contains(needle) -> 3
                        else -> 4
                    }
                }.thenBy { it.number.length - digitCount }.thenBy { it.number })
        }
        return matches.distinctBy { it.number }.take(200)
    }

'''
k = k[:a] + search_impl + k[b:]

# Make the typed search always use the dedicated search category selector if present.
k = k.replace(
    "val matches = db.search(q, categories.getOrNull(category.selectedItemPosition))",
    "val matches = db.search(q, categories.getOrNull(if (::searchCategory.isInitialized) searchCategory.selectedItemPosition else category.selectedItemPosition))"
)

# OCR: merge all preprocessing passes rather than discarding all but one pass.
# The PDF/image regex then sees candidates from each pass, reducing missed cards.
k = k.replace(
'''        return attempts.maxByOrNull { Regex("\\\\d{7,15}").findAll(normalizeDigits(it)).count() }.orEmpty()''',
'''        return attempts.joinToString("\\\\n")'''
)

main.write_text(k)
print("Applied exact dot-mask search and merged OCR passes for improved PDF extraction.")

# A dot must be enterable on every Android keyboard; use a text keyboard for the mask field.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
k = k.replace(
    "inputType = android.text.InputType.TYPE_CLASS_NUMBER",
    "inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD"
)
main.write_text(k)
print("Enabled literal dot entry in the card search box.")


# Prefer the PDF's embedded text layer before OCR. This preserves every printed card
# number in text-based PDFs and avoids losing digits due to OCR misses.
gradle_file = Path("app/build.gradle")
g = gradle_file.read_text()
dep = "implementation 'com.tom-roush:pdfbox-android:2.0.27.0'"
if dep not in g:
    g = g.replace("dependencies {", "dependencies {\n    " + dep)
gradle_file.write_text(g)

main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
if "import com.tom_roush.pdfbox.android.PDFBoxResourceLoader" not in k:
    k = k.replace(
        "package com.alkamel.cardfinder",
        "package com.alkamel.cardfinder\n\nimport com.tom_roush.pdfbox.android.PDFBoxResourceLoader\nimport com.tom_roush.pdfbox.pdmodel.PDDocument\nimport com.tom_roush.pdfbox.text.PDFTextStripper",
        1
    )

a = k.index("    private suspend fun readPdfNumbers(uri: Uri, cat: String, name: String): Int {")
b_candidates = [x for x in [
    k.find("\n    private fun ", a + 10),
    k.find("\n    private suspend fun ", a + 10),
    k.find("\n    fun ", a + 10)
] if x >= 0]
if not b_candidates:
    raise SystemExit("Could not locate end of PDF import method")
b = min(b_candidates)

pdf_method = '''    private suspend fun readPdfNumbers(uri: Uri, cat: String, name: String): Int {
        val extracted = linkedSetOf<String>()
        var textLayerRead = false
        try {
            PDFBoxResourceLoader.init(applicationContext)
            contentResolver.openInputStream(uri)?.use { input ->
                PDDocument.load(input).use { document ->
                    val pdfText = PDFTextStripper().getText(document)
                    val normalized = normalizeDigits(pdfText)
                    extracted.addAll(
                        Regex("(?<!\\\\\\\\d)\\\\\\\\d{11}(?!\\\\\\\\d)")
                            .findAll(normalized).map { it.value }
                    )
                    textLayerRead = extracted.isNotEmpty()
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("CardFinder", "PDF text extraction unavailable; falling back to OCR", e)
        }

        if (!textLayerRead) {
            val descriptor = contentResolver.openFileDescriptor(uri, "r")
                ?: throw IllegalStateException("تعذر فتح ملف PDF")
            descriptor.use { pfd ->
                val renderer = android.graphics.pdf.PdfRenderer(pfd)
                try {
                    for (i in 0 until renderer.pageCount) {
                        renderer.openPage(i).use { page ->
                            val scale = minOf(2.5f, 2200f / maxOf(page.width, page.height).toFloat())
                            val bitmap = Bitmap.createBitmap(
                                (page.width * scale).toInt().coerceAtLeast(1),
                                (page.height * scale).toInt().coerceAtLeast(1),
                                Bitmap.Config.ARGB_8888
                            )
                            try {
                                page.render(bitmap, null, null, android.graphics.pdf.PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                                val recognized = recognizeBest(bitmap)
                                val normalized = normalizeDigits(recognized)
                                extracted.addAll(
                                    Regex("(?<!\\\\\\\\d)\\\\\\\\d{11}(?!\\\\\\\\d)")
                                        .findAll(normalized).map { it.value }
                                )
                            } finally { bitmap.recycle() }
                        }
                        val percent = (((i + 1) * 100f) / renderer.pageCount.coerceAtLeast(1)).toInt()
                        runOnUiThread {
                            importProgress.progress = percent
                            showStatus("قراءة الملف $name: $percent% — الصفحة \${i + 1} من \${renderer.pageCount}")
                        }
                    }
                } finally { renderer.close() }
            }
        }

        if (extracted.isEmpty()) throw IllegalStateException("لم يتم العثور على أرقام كروت من 11 خانة في الملف")
        val cards = extracted.toList()
        val batches = cards.chunked(500)
        var added = 0
        batches.forEachIndexed { index, batch ->
            added += db.insertMany(batch, cat, name, 0)
            val percent = (((index + 1) * 100f) / batches.size.coerceAtLeast(1)).toInt()
            runOnUiThread {
                importProgress.progress = percent
                showStatus("حفظ كروت PDF: $percent% — \${index + 1}/\${batches.size}")
            }
        }
        runOnUiThread {
            showStatus("اكتمل استخراج \${cards.size} رقم فريد من الملف؛ أضيف جديداً $added")
        }
        return added
    }

'''
k = k[:a] + pdf_method + k[b:]
main.write_text(k)
print("Added PDF text-layer extraction as the primary path, with OCR fallback for scanned PDFs.")


# Backup/restore, clearer selectors, requested denomination palette, and faster bulk inserts.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
if "private val createBackup" not in k:
    anchor = '''    private val openFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) importDocument(uri)
    }'''
    add = anchor + '''
    private val createBackup = registerForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null) writeBackup(uri)
    }
    private val restoreBackup = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) confirmRestore(uri)
    }'''
    if anchor not in k: raise SystemExit("Backup picker insertion anchor missing")
    k = k.replace(anchor, add, 1)

anchor = '''        content.addView(action("تحديث إحصائيات المخزون", gold) { refreshStats() })'''
if anchor in k and "إنشاء نسخة احتياطية" not in k:
    k = k.replace(anchor, anchor + '''
        content.addView(txt("النسخ الاحتياطي وحماية المخزون", 19f, 0xFFFFFFFF.toInt(), true), lp())
        content.addView(action("إنشاء نسخة احتياطية واختيار مكان الحفظ", 0xFF13B981.toInt()) {
            createBackup.launch("alkamel-cards-backup.db")
        })
        content.addView(action("استعادة نسخة احتياطية", 0xFF4385F5.toInt()) {
            restoreBackup.launch(arrayOf("*/*"))
        })''', 1)

if "private fun writeBackup(uri: Uri)" not in k:
    anchor = "    private fun openCamera() {"
    methods = '''    private fun writeBackup(uri: Uri) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val database = db.writableDatabase
                    database.rawQuery("PRAGMA wal_checkpoint(FULL)", null).use { while (it.moveToNext()) {} }
                    val source = getDatabasePath("alkamel_cards.db")
                    if (!source.exists()) throw IllegalStateException("قاعدة البيانات غير موجودة")
                    contentResolver.openOutputStream(uri, "wt")?.use { output ->
                        source.inputStream().use { input -> input.copyTo(output) }
                    } ?: throw IllegalStateException("تعذر الكتابة إلى مكان النسخة")
                }
                showStatus("تم إنشاء النسخة الاحتياطية بنجاح في المكان الذي اخترته.")
                AlertDialog.Builder(this@MainActivity).setTitle("اكتملت النسخة الاحتياطية")
                    .setMessage("تم حفظ قاعدة بيانات الكروت. احتفظ بالملف في مكان آمن.")
                    .setPositiveButton("حسناً", null).show()
            } catch (e: Exception) {
                showStatus("فشل إنشاء النسخة الاحتياطية: \${e.localizedMessage}")
            }
        }
    }

    private fun confirmRestore(uri: Uri) {
        AlertDialog.Builder(this)
            .setTitle("تأكيد استعادة النسخة")
            .setMessage("سيتم استبدال قاعدة الكروت الحالية بمحتويات النسخة المختارة. أنشئ نسخة احتياطية من بياناتك الحالية أولاً. هل تريد المتابعة؟")
            .setNegativeButton("إلغاء", null)
            .setPositiveButton("استعادة") { _, _ -> restoreFromUri(uri) }
            .show()
    }

    private fun restoreFromUri(uri: Uri) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    val temp = File(cacheDir, "restore_cards_" + System.currentTimeMillis() + ".db")
                    contentResolver.openInputStream(uri)?.use { input ->
                        temp.outputStream().use { output -> input.copyTo(output) }
                    } ?: throw IllegalStateException("تعذر قراءة ملف النسخة")
                    val check = android.database.sqlite.SQLiteDatabase.openDatabase(
                        temp.absolutePath, null, android.database.sqlite.SQLiteDatabase.OPEN_READONLY
                    )
                    check.rawQuery("SELECT number, category, source, page FROM cards LIMIT 1", null).use { }
                    check.close()
                    db.close()
                    val target = getDatabasePath("alkamel_cards.db")
                    target.parentFile?.mkdirs()
                    File(target.absolutePath + "-wal").delete()
                    File(target.absolutePath + "-shm").delete()
                    temp.copyTo(target, overwrite = true)
                    temp.delete()
                    db = CardDb(this@MainActivity)
                    db.writableDatabase
                }
                refreshStats()
                showStatus("تمت استعادة النسخة الاحتياطية بنجاح.")
            } catch (e: Exception) {
                showStatus("تعذرت استعادة النسخة: \${e.localizedMessage}")
            }
        }
    }

'''
    if anchor not in k: raise SystemExit("Backup methods anchor missing")
    k = k.replace(anchor, methods + anchor, 1)

adapter = '''    private fun styledCategoryAdapter(): ArrayAdapter<String> =
        object : ArrayAdapter<String>(this, android.R.layout.simple_spinner_item, categories) {
            override fun getView(position: Int, convertView: View?, parent: android.view.ViewGroup): View {
                val view = super.getView(position, convertView, parent)
                (view as? TextView)?.apply {
                    setTextColor(0xFFFFFFFF.toInt())
                    textSize = 18f
                    gravity = Gravity.CENTER_VERTICAL or Gravity.RIGHT
                    setPadding(12, 8, 12, 8)
                }
                return view
            }
            override fun getDropDownView(position: Int, convertView: View?, parent: android.view.ViewGroup): View {
                val view = super.getDropDownView(position, convertView, parent)
                (view as? TextView)?.apply {
                    setTextColor(0xFFFFFFFF.toInt())
                    setBackgroundColor(0xFF17243A.toInt())
                    textSize = 17f
                    setPadding(18, 14, 18, 14)
                }
                return view
            }
        }

'''
if "private fun styledCategoryAdapter()" not in k:
    anchor = "    private fun label(text: String)"
    if anchor not in k: raise SystemExit("Adapter helper anchor missing")
    k = k.replace(anchor, adapter + anchor, 1)
k = k.replace('adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_item, categories).also {\n                it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)\n            }',
              'adapter = styledCategoryAdapter().also { it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item) }')

k = k.replace('val colors = listOf(green, blue, gold, purple)',
              'val colors = listOf(0xFFE53935.toInt(), purple, green, cyan)')
k = k.replace('txt("1) اختر فئة الكرت التي تريد البحث فيها", 17f, gold, true)',
              'txt("① اختر فئة الكرت التي تريد البحث فيها", 18f, 0xFFFFD54F.toInt(), true).apply { setPadding(8, 15, 8, 10) }')
k = k.replace('txt("2) اكتب الرقم الناقص أو الجزء الظاهر من الكرت", 17f, 0xFFFFFFFF.toInt(), true)',
              'txt("② اكتب الرقم الناقص أو الجزء الظاهر من الكرت", 18f, 0xFFFFFFFF.toInt(), true).apply { setPadding(8, 15, 8, 10) }')
k = k.replace('txt("حدد فئة الملف قبل اختياره", 16f, gold, true)',
              'txt("حدد فئة الملف قبل اختياره", 18f, 0xFFFFD54F.toInt(), true).apply { setPadding(8, 14, 8, 8) }')

k = k.replace(r'الصفحة \${i + 1} من \${renderer.pageCount}',
              r'الصفحة \${i + 1} من \${renderer.pageCount}')

if "override fun onOpen(db: android.database.sqlite.SQLiteDatabase)" not in k:
    anchor = "    override fun onUpgrade(db: android.database.sqlite.SQLiteDatabase, oldVersion: Int, newVersion: Int) {}"
    onopen = anchor + '''
    
    override fun onOpen(db: android.database.sqlite.SQLiteDatabase) {
        super.onOpen(db)
        db.execSQL("DELETE FROM cards WHERE id NOT IN (SELECT MIN(id) FROM cards GROUP BY number)")
        db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS idx_cards_number_unique ON cards(number)")
    }'''
    if anchor not in k: raise SystemExit("CardDb onOpen anchor missing")
    k = k.replace(anchor, onopen, 1)

a = k.index("    fun insertMany(numbers: List<String>, category: String, source: String, page: Int): Int {")
b = k.index("    fun search(query: String", a)
bulk = '''    fun insertMany(numbers: List<String>, category: String, source: String, page: Int): Int {
        val database = writableDatabase
        var added = 0
        database.beginTransaction()
        try {
            val statement = database.compileStatement(
                "INSERT OR IGNORE INTO cards(number, category, source, page) VALUES(?, ?, ?, ?)"
            )
            try {
                numbers.asSequence().map { it.trim() }.distinct().forEach { number ->
                    if (number.length == 11 && number.all { it in '0'..'9' }) {
                        statement.clearBindings()
                        statement.bindString(1, number)
                        statement.bindString(2, category)
                        statement.bindString(3, source)
                        statement.bindLong(4, page.toLong())
                        if (statement.executeInsert() != -1L) added++
                    }
                }
            } finally { statement.close() }
            database.setTransactionSuccessful()
        } finally { database.endTransaction() }
        return added
    }

'''
k = k[:a] + bulk + k[b:]
main.write_text(k)
print("Added user-selected backup/restore, high-contrast selectors, requested category colors, and faster indexed batch insertion.")


# Correct Kotlin interpolation in the PDF page progress label (avoid showing literal dollar signs).
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
k = k.replace(
    r'الصفحة \${i + 1} من \${renderer.pageCount}',
    'الصفحة \${i + 1} من \${renderer.pageCount}'
)
main.write_text(k)
print("Fixed PDF progress label interpolation.")


# OCR must use the same card-database search engine as typed queries.
# Search the selected category first, then all categories as a fallback so a wrong
# category selection cannot hide an otherwise valid match from the photographed card.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
a = k.index("    private fun readImage(uri: Uri) {")
b = k.index("    private suspend fun recognize(image: InputImage): Text =", a)
ocr_search = '''    private fun readImage(uri: Uri) {
        lifecycleScope.launch {
            showStatus("جارٍ قراءة الرقم من الصورة ثم البحث عنه في قاعدة الكروت...")
            results.removeAllViews()
            try {
                val bitmap = withContext(Dispatchers.IO) {
                    contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it) }
                        ?: throw IllegalStateException("تعذر فتح الصورة المختارة")
                }
                val attempts = recognizeAttempts(bitmap)
                bitmap.recycle()

                val fragments = attempts.flatMap { text ->
                    normalizeDigits(text).split(Regex("[^0-9]+"))
                        .map { it.filter { ch -> ch in '0'..'9' } }
                        .filter { it.length >= 3 }
                }.distinct().sortedWith(compareByDescending<String> { it.length }.thenBy { it })

                data class OcrMatch(val record: CardRecord, val sourceDigits: String)
                val found = linkedMapOf<String, OcrMatch>()
                val selectedCategory = categories.getOrNull(category.selectedItemPosition)

                // First, search every OCR fragment using exactly the same DB matcher as typed search.
                // The selected category is preferred, then the complete inventory is checked.
                for (fragment in fragments) {
                    if (fragment.length > 11) continue
                    db.search(fragment, selectedCategory).forEach { record ->
                        found.putIfAbsent(record.number, OcrMatch(record, fragment))
                    }
                }
                if (found.isEmpty()) {
                    for (fragment in fragments) {
                        if (fragment.length > 11) continue
                        db.search(fragment).forEach { record ->
                            found.putIfAbsent(record.number, OcrMatch(record, fragment))
                        }
                    }
                }

                // If OCR split a card number into two visible groups, join them in reading order
                // and run the same subsequence search against the database.
                for (firstIndex in fragments.indices) {
                    for (secondIndex in fragments.indices) {
                        if (firstIndex == secondIndex) continue
                        val first = fragments[firstIndex]
                        val second = fragments[secondIndex]
                        if (first.length < 3 || second.length < 3 || first.length + second.length > 11) continue
                        val joined = first + second
                        val primary = db.search(joined, selectedCategory)
                        val matches = if (primary.isNotEmpty()) primary else db.search(joined)
                        matches.forEach { record ->
                            val firstAt = record.number.indexOf(first)
                            val secondAt = if (firstAt >= 0) record.number.indexOf(second, firstAt + first.length) else -1
                            if (firstAt >= 0 && secondAt >= firstAt + first.length) {
                                found.putIfAbsent(record.number, OcrMatch(record, joined))
                            }
                        }
                    }
                }

                // A full 11-digit OCR read is an exact lookup too.
                fragments.filter { it.length == 11 }.forEach { number ->
                    db.search(number).forEach { record -> found.putIfAbsent(record.number, OcrMatch(record, number)) }
                }

                if (found.isEmpty()) {
                    showStatus("قرأت أرقاماً من الصورة، لكن لم أجد لها كرتاً مطابقاً في المخزون. تأكد من استيراد ملف الفئة الصحيحة.")
                    results.addView(TextView(this@MainActivity).apply {
                        text = "الأرقام التي قرأتها الكاميرا: " + fragments.joinToString("  |  ").ifBlank { "لم يتم التعرف على أرقام" } +
                            "\\\\n\\\\nجرّب تحسين وضوح الصورة أو اختر فئة الكرت الصحيحة ثم أعد المحاولة."
                        textSize = 15f
                        setTextColor(0xFFFFFFFF.toInt())
                        setPadding(12, 12, 12, 12)
                    })
                } else {
                    val sorted = found.values.sortedWith(
                        compareBy<OcrMatch> { if (it.record.category == selectedCategory) 0 else 1 }
                            .thenBy { it.record.number }
                    )
                    showStatus("بحث الصورة اكتمل: عثرت على " + sorted.size + " كرت مطابق.")
                    sorted.take(100).forEachIndexed { index, match ->
                        val card = LinearLayout(this@MainActivity).apply {
                            orientation = LinearLayout.VERTICAL
                            setPadding(14, 12, 14, 12)
                            background = android.graphics.drawable.GradientDrawable().apply {
                                setColor(0xFF111A2E.toInt())
                                setStroke(2, 0xFF20B981.toInt())
                                cornerRadius = 18f
                            }
                        }
                        card.addView(TextView(this@MainActivity).apply {
                            text = "نتيجة مطابقة رقم " + (index + 1)
                            textSize = 13f; setTextColor(0xFFFFB300.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "الرقم المقروء من الصورة: " + match.sourceDigits
                            textSize = 15f; setTextColor(0xFFCAD3E2.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "رقم الكرت الكامل: " + match.record.number
                            textSize = 22f; typeface = android.graphics.Typeface.DEFAULT_BOLD
                            setTextColor(0xFFFFFFFF.toInt())
                        })
                        card.addView(TextView(this@MainActivity).apply {
                            text = "الفئة: " + match.record.category
                            textSize = 14f; setTextColor(0xFF20C7E8.toInt())
                        })
                        results.addView(card)
                    }
                }
            } catch (e: Exception) {
                showStatus("تعذرت قراءة الصورة والبحث عنها: \${e.localizedMessage}")
            }
        }
    }

    private suspend fun recognizeAttempts(bitmap: Bitmap): List<String> {
        val attempts = mutableListOf<String>()
        attempts.add(recognize(InputImage.fromBitmap(bitmap, 0)).text)
        for (threshold in listOf(false, true)) {
            val processed = enhanceForDarkDigits(bitmap, threshold)
            try { attempts.add(recognize(InputImage.fromBitmap(processed, 0)).text) }
            finally { processed.recycle() }
        }
        return attempts
    }

'''
k = k[:a] + ocr_search + k[b:]
main.write_text(k)
print("Unified camera/gallery OCR results with the typed database search and all-category fallback.")


# Restore OCR preprocessing helpers that were removed when the image flow was unified.
# PDF extraction also calls recognizeBest, while the camera/gallery flow uses
# recognizeAttempts; both share these preprocessing implementations.
main = Path("app/src/main/java/com/alkamel/cardfinder/MainActivity.kt")
k = main.read_text()
anchor = "    private suspend fun recognize(image: InputImage): Text ="
if "private suspend fun recognizeBest(bitmap: Bitmap): String" not in k:
    helpers = '''    private suspend fun recognizeBest(bitmap: Bitmap): String {
        val attempts = recognizeAttempts(bitmap)
        return attempts.maxByOrNull { text ->
            Regex("\\\\d{7,15}").findAll(normalizeDigits(text)).count()
        }.orEmpty()
    }

    private fun enhanceForDarkDigits(source: Bitmap, threshold: Boolean): Bitmap {
        val scale = minOf(2.0f, 2200f / maxOf(source.width, source.height).toFloat()).coerceAtLeast(1f)
        val width = (source.width * scale).toInt().coerceAtLeast(1)
        val height = (source.height * scale).toInt().coerceAtLeast(1)
        val output = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply {
            colorFilter = ColorMatrixColorFilter(ColorMatrix().apply { setSaturation(0f) })
        }
        Canvas(output).drawBitmap(source, null, android.graphics.Rect(0, 0, width, height), paint)
        val pixels = IntArray(width * height)
        output.getPixels(pixels, 0, width, 0, 0, width, height)
        for (index in pixels.indices) {
            val pixel = pixels[index]
            val gray = (Color.red(pixel) * 0.299 + Color.green(pixel) * 0.587 + Color.blue(pixel) * 0.114).toInt()
            val value = if (threshold) {
                if (gray < 175) 0 else 255
            } else {
                ((gray - 128) * 1.8 + 128).toInt().coerceIn(0, 255)
            }
            pixels[index] = Color.rgb(value, value, value)
        }
        output.setPixels(pixels, 0, width, 0, 0, width, height)
        return output
    }

'''
    if anchor not in k: raise SystemExit("recognize() anchor missing")
    k = k.replace(anchor, helpers + anchor, 1)
main.write_text(k)
print("Restored OCR preprocessing helper functions required by camera/gallery and PDF OCR.")

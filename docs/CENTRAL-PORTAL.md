# القيادة المركزية

الواجهة الحالية انتقلت إلى https://amer-ai-store.github.io/Mohammad-Ahmed-Ghannam/#jawafa-trustsoft
مع نسخة مباشرة https://amer-ai-store.github.io/Mohammad-Ahmed-Ghannam/trustsoft/.
GitHub Pages يستضيف HTML/JS/CSS فقط، وتبقى API وD1 والوكيل على Cloudflare دون تبديل اعتماد الوكيل أو broker.
تستخدم واجهة Pages جلسة Bearer موقعة مدتها15دقيقة في الذاكرة فقط. لها مجال توقيع منفصل عن Cookie،
ودور وaudience وإصدار مفتاح وتاريخ انتهاء؛ تدوير المفتاح يلغي الجلسات السابقة للدور.
CORS يسمح بأصل amer-ai-store.github.io فقط والمسارات المحددة، دون cookies أو wildcard أو CORS للوكيل.
كل مستودعات ذلك الأصل ضمن حد الثقة نفسه؛ المسار ليس عزلًا أمنيًا. الجلسة متاحة لJavaScript بخلاف Cookie HttpOnly.
لا مفاتيح في ملفات Pages أو URL أو localStorage/sessionStorage. إغلاق الإطار ومغادرة القسم يمسحان الواجهة.
واجهات Cloudflare الحالية تبقى قابلة للاستخدام بجلساتها السابقة. مصدر ملفات Pages هو web/ في هذا المشروع،
مع HTML مهيأ بعنوان API ثابت وCSP وملف host-integration.js في مستودع القيادة.
التحقق: scripts/check_portal_api.py على workerd/D1 محلي، scripts/check_free_worker.py للقناة السابقة،
واختباراتواجهة وChromium لمحاكاة Android مع الدخول إلىAPIالحقيقية. هذه الاختبارات ليست دليل SQLإنتاج.

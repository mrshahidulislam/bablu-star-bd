BABLU STAR BD — Inject Cloud deployment

1) আগে বর্তমান bot.py এবং SQLite database-এর ব্যাকআপ নিন।
2) এই প্যাকেজের bot_professional.py-কে GitHub repository-তে bot.py নামে আপলোড করুন (অথবা Inject Cloud entrypoint অনুযায়ী ফাইলের নাম দিন)।
3) requirements.txt-এ python-telegram-bot==21.10 রাখুন।
4) Inject Cloud Environment Variables:
   BOT_TOKEN = আপনার Telegram bot token
   ADMIN_ID = আপনার numeric Telegram user ID
   DB_PATH = স্থায়ী storage-এ bablu_star_bd.sqlite3 (ঐচ্ছিক; default local path)
5) Redeploy করুন। /start এবং /admin দিয়ে পরীক্ষা করুন।

এই সংস্করণে আছে:
- ৮টি নির্ধারিত প্ল্যাটফর্ম (Facebook, YouTube, Instagram, TikTok, X, Snapchat, Telegram, LinkedIn)
- Admin job posting deposit bypass এবং buyer fee exemption (Admin job payout platform liability হিসেবে ধরে)
- Deposit: bKash/Nagad method button, configured receiver number, Txn ID submission
- Freelancer withdrawal: bKash/Nagad/Recharge method first, USD amount input, USD-to-BDT conversion, configurable 15% fee calculation, net amount preview
- Admin dashboard: registered user count, recent user list, user search, Ban/Unban and ban reason
- Editable welcome/rules/payment text, support info, payment numbers, minimums, fees and exchange rate
- SQLite additive migration for ban_reason and is_admin_job

গুরুত্বপূর্ণ সীমাবদ্ধতা:
- Telegram Bot API কোনো ডিভাইসের IMEI/device ID দেয় না। তাই একই ডিভাইসে কতটি Telegram account আছে তা নির্ভুলভাবে গণনা করা যায় না; এখানে bot-এ নিবন্ধিত Telegram user ID গণনা হয়।
- এই কোডে ৮টি platform আছে, কিন্তু platform অনুযায়ী Like/Follow/Comment/Repost আলাদা task এবং ৩-কাজের bundle pricing এখনও সম্পূর্ণ implement হয়নি।
- পুরোনো DB schema ভিন্ন হলে migration backup ছাড়া চালাবেন না।
- Syntax check সফল হলেও Telegram/Inject Cloud live test করা হয়নি। প্রথমে test bot ও test accounts দিয়ে পরীক্ষা করুন।
- Admin-posted jobs deposit ছাড়াই publish হয়; তাই তাদের worker payout আপনার platform-এর অর্থ থেকে যাবে। Platform funding source আলাদাভাবে পরিকল্পনা করুন।
- কোনো real payment gateway integration নেই; deposit/withdraw/recharge manual admin approval ও fulfillment-এর জন্য।

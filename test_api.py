import urllib.request, json, time
base = 'http://localhost:8000'

# Test /faqs
r = urllib.request.urlopen(base + '/faqs')
faqs = json.loads(r.read())
print('/faqs:', faqs['total'], 'FAQs')
print('  Top:', faqs['faqs'][0]['faq_question'], '(support={})'.format(faqs['faqs'][0]['support_count']))

# Test /search  
req = urllib.request.Request(
    base + '/search',
    data=json.dumps({'query': 'track my order', 'top_k': 3}).encode(),
    headers={'Content-Type': 'application/json'}
)
t0 = time.time()
r = urllib.request.urlopen(req)
ms = (time.time() - t0) * 1000
data = json.loads(r.read())
print('/search: {} results in {:.1f}ms'.format(data['count'], ms))
print('  Top result:', data['results'][0]['faq_question'])
print('  Score:', data['results'][0]['similarity_score'])

# Test /clusters
r = urllib.request.urlopen(base + '/clusters')
cl = json.loads(r.read())
print('/clusters:', cl['total_clusters'], 'clusters')

# Test /analytics
r = urllib.request.urlopen(base + '/analytics')
an = json.loads(r.read())
s = an['summary']
print('/analytics: total={}, faqs={}, noise={}%'.format(
    s['total_conversations'], s['total_faqs_generated'], s['noise_ratio_percent']))

print('\nALL ENDPOINTS OK')

curl https://www.right.codes/codex/v1/responses \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-5da17c579fac479bbaed0ed69f94fc8c' \
  -d '{
    "model": "gpt-5.6-terra",
    "input": [
      {
        "type": "message",
        "role": "user",
        "content": [
          {
            "type": "input_text",
            "text": "你好"
          }
        ]
      }
    ],
    "stream": false
  }'
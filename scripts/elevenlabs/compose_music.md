# my api key
sk_34f78cb70feab4bedbb1a3434b11ab0552d2cb0ebcb7bdb5

# Compose music

POST https://api.elevenlabs.io/v1/music
Content-Type: application/json

Compose a song from a prompt or a composition plan.

Reference: https://elevenlabs.io/docs/api-reference/music/compose

## OpenAPI Specification

```yaml
openapi: 3.1.0
info:
  title: api
  version: 1.0.0
paths:
  /v1/music:
    post:
      operationId: compose
      summary: Compose Music
      description: Compose a song from a prompt or a composition plan.
      tags:
        - subpackage_music
      parameters:
        - name: output_format
          in: query
          description: >-
            Output format of the generated audio. Formatted as
            codec_sample_rate_bitrate. So an mp3 with 22.05kHz sample rate at
            32kbs is represented as mp3_22050_32. MP3 with 192kbps bitrate
            requires you to be subscribed to Creator tier or above. PCM with
            44.1kHz sample rate requires you to be subscribed to Pro tier or
            above. Note that the μ-law format (sometimes written mu-law, often
            approximated as u-law) is commonly used for Twilio audio inputs.
          required: false
          schema:
            $ref: '#/components/schemas/AllowedOutputFormats'
        - name: xi-api-key
          in: header
          required: false
          schema:
            type: string
      responses:
        '200':
          description: The generated audio file in the format specified
          content:
            application/octet-stream:
              schema:
                type: string
                format: binary
        '422':
          description: Validation Error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HTTPValidationError'
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Body_Compose_music_v1_music_post'
servers:
  - url: https://api.elevenlabs.io
  - url: https://api.us.elevenlabs.io
  - url: https://api.eu.residency.elevenlabs.io
  - url: https://api.in.residency.elevenlabs.io
components:
  schemas:
    AllowedOutputFormats:
      type: string
      enum:
        - mp3_22050_32
        - mp3_24000_48
        - mp3_44100_32
        - mp3_44100_64
        - mp3_44100_96
        - mp3_44100_128
        - mp3_44100_192
        - pcm_8000
        - pcm_16000
        - pcm_22050
        - pcm_24000
        - pcm_32000
        - pcm_44100
        - pcm_48000
        - ulaw_8000
        - alaw_8000
        - opus_48000_32
        - opus_48000_64
        - opus_48000_96
        - opus_48000_128
        - opus_48000_192
      title: AllowedOutputFormats
    TimeRange:
      type: object
      properties:
        start_ms:
          type: integer
        end_ms:
          type: integer
      required:
        - start_ms
        - end_ms
      title: TimeRange
    SectionSource:
      type: object
      properties:
        song_id:
          type: string
          description: >-
            The ID of the song to source the section from. You can find the song
            ID in the response headers when you generate a song.
        range:
          $ref: '#/components/schemas/TimeRange'
          description: The range to extract from the source song.
        negative_ranges:
          type: array
          items:
            $ref: '#/components/schemas/TimeRange'
          description: The ranges to exclude from the 'range'.
      required:
        - song_id
        - range
      title: SectionSource
    SongSection:
      type: object
      properties:
        section_name:
          type: string
          description: The name of the section. Must be between 1 and 100 characters.
        positive_local_styles:
          type: array
          items:
            type: string
          description: >-
            The styles and musical directions that should be present in this
            section. Use English language for best result.
        negative_local_styles:
          type: array
          items:
            type: string
          description: >-
            The styles and musical directions that should not be present in this
            section. Use English language for best result.
        duration_ms:
          type: integer
          description: >-
            The duration of the section in milliseconds. Must be between 3000ms
            and 120000ms.
        lines:
          type: array
          items:
            type: string
          description: The lyrics of the section. Max 200 characters per line.
        source_from:
          oneOf:
            - $ref: '#/components/schemas/SectionSource'
            - type: 'null'
          description: >-
            Optional source to extract the section from. Used for inpainting.
            Only available to enterprise clients with access to the inpainting
            feature.
      required:
        - section_name
        - positive_local_styles
        - negative_local_styles
        - duration_ms
        - lines
      title: SongSection
    MusicPrompt:
      type: object
      properties:
        positive_global_styles:
          type: array
          items:
            type: string
          description: >-
            The styles and musical directions that should be present in the
            entire song. Use English language for best result.
        negative_global_styles:
          type: array
          items:
            type: string
          description: >-
            The styles and musical directions that should not be present in the
            entire song. Use English language for best result.
        sections:
          type: array
          items:
            $ref: '#/components/schemas/SongSection'
          description: The sections of the song.
      required:
        - positive_global_styles
        - negative_global_styles
        - sections
      title: MusicPrompt
    BodyComposeMusicV1MusicPostModelId:
      type: string
      enum:
        - music_v1
      default: music_v1
      description: The model to use for the generation.
      title: BodyComposeMusicV1MusicPostModelId
    Body_Compose_music_v1_music_post:
      type: object
      properties:
        prompt:
          type:
            - string
            - 'null'
          description: >-
            A simple text prompt to generate a song from. Cannot be used in
            conjunction with `composition_plan`.
        composition_plan:
          oneOf:
            - $ref: '#/components/schemas/MusicPrompt'
            - type: 'null'
          description: >-
            A detailed composition plan to guide music generation. Cannot be
            used in conjunction with `prompt`.
        music_length_ms:
          type:
            - integer
            - 'null'
          description: >-
            The length of the song to generate in milliseconds. Used only in
            conjunction with `prompt`. Must be between 3000ms and 600000ms.
            Optional - if not provided, the model will choose a length based on
            the prompt.
        model_id:
          $ref: '#/components/schemas/BodyComposeMusicV1MusicPostModelId'
          description: The model to use for the generation.
        seed:
          type:
            - integer
            - 'null'
          description: >-
            Random seed to initialize the music generation process. Providing
            the same seed with the same parameters can help achieve more
            consistent results, but exact reproducibility is not guaranteed and
            outputs may change across system updates. Cannot be used in
            conjunction with prompt.
        force_instrumental:
          type: boolean
          default: false
          description: >-
            If true, guarantees that the generated song will be instrumental. If
            false, the song may or may not be instrumental depending on the
            `prompt`. Can only be used with `prompt`.
        respect_sections_durations:
          type: boolean
          default: true
          description: >-
            Controls how strictly section durations in the `composition_plan`
            are enforced. Only used with `composition_plan`. When set to true,
            the model will precisely respect each section's `duration_ms` from
            the plan. When set to false, the model may adjust individual section
            durations which will generally lead to better generation quality and
            improved latency, while always preserving the total song duration
            from the plan.
        store_for_inpainting:
          type: boolean
          default: false
          description: >-
            Whether to store the generated song for inpainting. Only available
            to enterprise clients with access to the inpainting feature.
        sign_with_c2pa:
          type: boolean
          default: false
          description: >-
            Whether to sign the generated song with C2PA. Applicable only for
            mp3 files.
      title: Body_Compose_music_v1_music_post
    ValidationErrorLocItems:
      oneOf:
        - type: string
        - type: integer
      title: ValidationErrorLocItems
    ValidationError:
      type: object
      properties:
        loc:
          type: array
          items:
            $ref: '#/components/schemas/ValidationErrorLocItems'
        msg:
          type: string
        type:
          type: string
      required:
        - loc
        - msg
        - type
      title: ValidationError
    HTTPValidationError:
      type: object
      properties:
        detail:
          type: array
          items:
            $ref: '#/components/schemas/ValidationError'
      title: HTTPValidationError

```

## SDK Code Examples

```typescript
import { ElevenLabsClient } from "@elevenlabs/elevenlabs-js";

async function main() {
    const client = new ElevenLabsClient();
    await client.music.compose({});
}
main();

```

```python
from elevenlabs import ElevenLabs

client = ElevenLabs()

client.music.compose()

```

```go
package main

import (
	"fmt"
	"strings"
	"net/http"
	"io"
)

func main() {

	url := "https://api.elevenlabs.io/v1/music"

	payload := strings.NewReader("{}")

	req, _ := http.NewRequest("POST", url, payload)

	req.Header.Add("Content-Type", "application/json")

	res, _ := http.DefaultClient.Do(req)

	defer res.Body.Close()
	body, _ := io.ReadAll(res.Body)

	fmt.Println(res)
	fmt.Println(string(body))

}
```

```ruby
require 'uri'
require 'net/http'

url = URI("https://api.elevenlabs.io/v1/music")

http = Net::HTTP.new(url.host, url.port)
http.use_ssl = true

request = Net::HTTP::Post.new(url)
request["Content-Type"] = 'application/json'
request.body = "{}"

response = http.request(request)
puts response.read_body
```

```java
import com.mashape.unirest.http.HttpResponse;
import com.mashape.unirest.http.Unirest;

HttpResponse<String> response = Unirest.post("https://api.elevenlabs.io/v1/music")
  .header("Content-Type", "application/json")
  .body("{}")
  .asString();
```

```php
<?php
require_once('vendor/autoload.php');

$client = new \GuzzleHttp\Client();

$response = $client->request('POST', 'https://api.elevenlabs.io/v1/music', [
  'body' => '{}',
  'headers' => [
    'Content-Type' => 'application/json',
  ],
]);

echo $response->getBody();
```

```csharp
using RestSharp;

var client = new RestClient("https://api.elevenlabs.io/v1/music");
var request = new RestRequest(Method.POST);
request.AddHeader("Content-Type", "application/json");
request.AddParameter("application/json", "{}", ParameterType.RequestBody);
IRestResponse response = client.Execute(request);
```

```swift
import Foundation

let headers = ["Content-Type": "application/json"]
let parameters = [] as [String : Any]

let postData = JSONSerialization.data(withJSONObject: parameters, options: [])

let request = NSMutableURLRequest(url: NSURL(string: "https://api.elevenlabs.io/v1/music")! as URL,
                                        cachePolicy: .useProtocolCachePolicy,
                                    timeoutInterval: 10.0)
request.httpMethod = "POST"
request.allHTTPHeaderFields = headers
request.httpBody = postData as Data

let session = URLSession.shared
let dataTask = session.dataTask(with: request as URLRequest, completionHandler: { (data, response, error) -> Void in
  if (error != nil) {
    print(error as Any)
  } else {
    let httpResponse = response as? HTTPURLResponse
    print(httpResponse)
  }
})

dataTask.resume()
```
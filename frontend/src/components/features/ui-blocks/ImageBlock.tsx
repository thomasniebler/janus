import React from 'react';
import { Download, ImageOff } from 'lucide-react';

import { IMAGE_URL_PATTERN, type ImageUIBlock } from '../../../lib/uiBlocks';
import { BlockFrame } from './BlockFrame';

/**
 * An image Janus serves itself (`/api/images/<id>`, JAN-253). Without such a URL,
 * or when it fails to load, the alt text is shown instead; external URLs never load.
 * A loaded image gets a download link to the same authenticated URL.
 */
export const ImageBlock: React.FC<{ block: ImageUIBlock }> = ({ block }) => {
  const [failed, setFailed] = React.useState(false);
  const url = block.url && IMAGE_URL_PATTERN.test(block.url) ? block.url : null;
  return (
    <BlockFrame kind="Image" title={block.title}>
      <figure>
        {url && !failed ? (
          <img
            src={url}
            alt={block.alt}
            loading="lazy"
            onError={() => setFailed(true)}
            className="max-h-80 max-w-full rounded border border-gray-200 dark:border-gray-700"
          />
        ) : (
          <div className="flex items-center gap-2 rounded border border-dashed border-gray-300 p-3 text-xs text-gray-500 dark:border-gray-600 dark:text-gray-400">
            <ImageOff size={14} aria-hidden="true" />
            <span>Image unavailable: {block.alt}</span>
          </div>
        )}
        {(block.caption || (url && !failed)) && (
          <figcaption className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
            {block.caption && <span>{block.caption}</span>}
            {url && !failed && (
              <a
                href={url}
                download
                className="inline-flex items-center gap-1 font-medium text-primary hover:underline uiv2:text-v2-accent uiv3:text-v3-accent-bright"
              >
                <Download size={12} aria-hidden="true" />
                Download
              </a>
            )}
          </figcaption>
        )}
      </figure>
    </BlockFrame>
  );
};

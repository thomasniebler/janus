import { render, type RenderOptions } from '@testing-library/react';
import { createElement, type ComponentType } from 'react';

export function renderStory<TProps extends object>(
  Story: ComponentType<Partial<TProps>>,
  props?: Partial<TProps>,
  options?: Omit<RenderOptions, 'queries'>
) {
  return render(createElement(Story, props), options);
}
